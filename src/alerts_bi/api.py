"""HTTP trigger surface (design section 7.8).

A convenience wrapper around exactly what the CLI does, so a run can be started from a
browser or a `curl` line instead of a shell in the repository. It adds no analysis of its
own: every endpoint either loads the registry, calls :func:`execute_run` and
:func:`persist_run` with the same arguments the CLI passes, or renders a stored run from
committed SQL rows through :func:`build_run_outputs`. There is no second code path that
could drift from the command line.

Scope discipline is unchanged. A run still names one team and never defaults to all of
them, ``run_at`` is still captured once per run, and the four approved output files are
still written exactly as the CLI writes them.

Built on FastAPI so the request contract is declared rather than parsed by hand, the
OpenAPI document is generated from that contract instead of maintained beside it, and the
approved interactive frontend has a base to build on.

CONCURRENCY. A run is minutes of blocking Elasticsearch and SQL work, so the run endpoint
is a plain ``def`` and FastAPI dispatches it to the thread pool rather than blocking the
event loop. Runs are additionally serialized by a lock: two concurrent requests for one
team and clock derive the same deterministic ``run_id`` and would race to replace each
other's rows.

SECURITY. There is no authentication. Every request triggers real Elasticsearch queries and
real SQL writes, and a run can call the on-prem model. The listener therefore binds to
loopback by default; ``--host`` can widen it, and doing so on a shared machine exposes an
unauthenticated write endpoint to that network.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from alerts_bi.config import AppConfig, load_config
from alerts_bi.db.connection import connect
from alerts_bi.db.repositories import get_latest_run, persist_run
from alerts_bi.es.client import EsClient
from alerts_bi.es.reader import V1_INDEX
from alerts_bi.llm.client import LlmClient
from alerts_bi.llm.fake import FakeLlmClient
from alerts_bi.logging_setup import log, redact_error
from alerts_bi.registry import RegistryError, load_registry
from alerts_bi.report.html import escape_html
from alerts_bi.report.render import OUTPUT_FILES, build_run_outputs, render_run_report
from alerts_bi.run.orchestrator import RunSummary, execute_run, select_llm_client
from alerts_bi.versions import APP_VERSION

__all__ = ["Settings", "build_app", "serve"]

#: How a run may treat the model. The names and their meanings are the CLI's: ``live``
#: matches the CLI's default (which still yields no model when LLM_ENABLED is false),
#: ``fake`` matches ``--fake-llm``, ``off`` matches ``--no-llm``.
LlmMode = Literal["live", "fake", "off"]

#: Only the four approved outputs are addressable. Declared as a type so an unknown name is
#: refused by validation before any handler runs.
OutputFile = Literal["scorecard.html", "daily_metrics.csv", "rule_counts.csv", "alert_worklist.csv"]

CSV_MEDIA_TYPE = "text/csv; charset=utf-8"


class Settings:
    """Process-wide configuration for the surface.

    ``run_lock`` serializes runs. Two concurrent requests for the same team and clock would
    derive the same deterministic ``run_id`` and race to replace each other's rows; two for
    different teams would simply compete for the same Elasticsearch and SQL capacity for no
    gain, since a run reads one team at a time by design.
    """

    def __init__(
        self,
        config: AppConfig,
        registry_path: str | None = None,
        database: str | None = None,
        out_root: Path | None = None,
    ) -> None:
        self.config = config
        self.registry_path = registry_path
        self.database = database or config.sql.database
        self.out_root = out_root or Path("out")
        self.run_lock = threading.Lock()


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


# ------------------------------------------------------------------------- models


class TeamOut(BaseModel):
    team_id: str
    display_name: str
    v1_operators: list[str]
    v2_operator: str | None
    panels: int


class TeamsOut(BaseModel):
    teams: list[TeamOut]


class CheckOut(BaseModel):
    ok: bool
    error: str | None = None
    database: str | None = None


class HealthOut(BaseModel):
    ok: bool
    version: str
    checks: dict[str, CheckOut]


class RunRequest(BaseModel):
    """The parameters of one run.

    ``team`` has no default on purpose: a run names one team, and a surface that filled it
    in would turn a scope rule into a convenience.
    """

    team: str = Field(min_length=1, description="registry team_id; a run never defaults")
    run_at: datetime | None = Field(
        default=None, description="freeze run_at (ISO 8601 UTC); defaults to now"
    )
    llm: LlmMode = Field(
        default="live", description="live: the on-prem model; fake: deterministic; off: skip"
    )


class VolumeOut(BaseModel):
    rows: int
    distinct: int


class RunOut(BaseModel):
    run_id: str
    team_id: str
    phase: str
    phase2_readiness_pct: float | None
    v1: VolumeOut
    v2: VolumeOut
    llm_assessed: bool
    llm_eligible: int
    out_dir: str
    scorecard: str
    files: dict[str, str]


# ------------------------------------------------------------------------ helpers


def _load_teams(settings: Settings) -> list[TeamOut]:
    path = settings.registry_path
    try:
        loaded = load_registry(path) if path else load_registry()
    except RegistryError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from None
    return [
        TeamOut(
            team_id=team.team_id,
            display_name=team.display_name,
            v1_operators=list(team.v1_operators),
            v2_operator=team.v2_operator,
            panels=len(team.panels),
        )
        for team in loaded.teams
    ]


def _select_client(config: AppConfig, mode: LlmMode) -> tuple[LlmClient | None, str | None]:
    # The deterministic fake stamps its own model_version onto the run record, so a run
    # started this way can never be mistaken for a live one after the fact.
    if mode == "fake":
        return FakeLlmClient(), None
    return select_llm_client(config, use_llm=mode == "live")


def _execute(settings: Settings, body: RunRequest) -> tuple[RunSummary, dict[str, str], Path]:
    run_at = body.run_at or datetime.now(UTC)
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=UTC)
    client, reason = _select_client(settings.config, body.llm)

    if not settings.run_lock.acquire(blocking=False):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a run is already in progress; runs are serialized so two cannot race to write "
            "the same rows",
        )
    try:
        es_client = EsClient(settings.config.es)
        with connect(settings.config.sql, settings.database) as db:
            payload, summary = execute_run(
                team_id=body.team,
                run_at=run_at,
                config=settings.config,
                es_client=es_client,
                llm_client=client,
                llm_disabled_reason=reason,
                registry_path=settings.registry_path,
                db=db,
            )
            persist_run(db, payload)
            log.info("run.persisted", run_id=summary.run_id, team_id=summary.team_id)

            # The file contract is the same whether a run is started here or from the
            # command line: the four approved files, written once, under out/.
            out_dir = settings.out_root / summary.run_id[:16]
            render_run_report(db, summary.run_id, out_dir)
            return summary, build_run_outputs(db, summary.run_id), out_dir
    except RegistryError as exc:
        # An unknown team or a malformed entry is the caller's mistake, not a server fault,
        # and the registry is validated before any alert is queried.
        detail = "; ".join([str(exc), *exc.details]) if exc.details else str(exc)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    finally:
        settings.run_lock.release()


def _resolve(settings: Settings, run_id: str, team: str | None) -> tuple[str, dict[str, str]]:
    with connect(settings.config.sql, settings.database) as db:
        resolved = run_id
        if run_id == "latest":
            if not team:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "latest requires ?team=<team_id>")
            latest = get_latest_run(db, team)
            if latest is None:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND, f"no completed run stored for team {team}"
                )
            resolved = str(latest["run_id"])
        try:
            return resolved, build_run_outputs(db, resolved)
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


def _wants_html(request: Request) -> bool:
    """A browser gets HTML; anything asking for JSON gets JSON.

    Explicit ``application/json`` wins, because a browser's Accept header lists it too,
    just after ``text/html``.
    """
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return False
    return "text/html" in accept or "*/*" in accept or accept == ""


# --------------------------------------------------------------------------- app


def build_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="Alerts BI",
        version=APP_VERSION,
        description=(
            "Starts one team's weekly alert-quality run and serves its scorecard. A wrapper "
            "over the same pipeline the command line drives; it performs no analysis of its "
            "own, and reports are rendered only from committed SQL rows."
        ),
    )
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> Response:
        """Answer a browser in HTML and a script in JSON.

        The run form posts from the index page, so a validation failure has to be readable
        without a JSON viewer.
        """
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'][1:])}: {error['msg']}"
            for error in exc.errors()
        )
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
        if _wants_html(request):
            return HTMLResponse(_error_page(code, detail), status_code=code)
        return JSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=code)

    @app.exception_handler(HTTPException)
    async def _http_handler(request: Request, exc: HTTPException) -> Response:
        if _wants_html(request):
            return HTMLResponse(
                _error_page(exc.status_code, str(exc.detail)), status_code=exc.status_code
            )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(settings: SettingsDep) -> HTMLResponse:
        return HTMLResponse(_index_page(_load_teams(settings)))

    @app.get("/healthz", response_model=HealthOut, summary="Liveness and dependency checks")
    def healthz(settings: SettingsDep, response: Response) -> HealthOut:
        checks: dict[str, CheckOut] = {}
        try:
            checks["elasticsearch"] = CheckOut(
                ok=EsClient(settings.config.es).index_exists(V1_INDEX)
            )
        except Exception as exc:  # report the failure rather than raising
            checks["elasticsearch"] = CheckOut(ok=False, error=redact_error(exc))
        try:
            with connect(settings.config.sql, settings.database) as db:
                db.query("SELECT 1 AS ok")
            checks["sql_server"] = CheckOut(ok=True, database=settings.database)
        except Exception as exc:
            checks["sql_server"] = CheckOut(ok=False, error=redact_error(exc))

        healthy = all(check.ok for check in checks.values())
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthOut(ok=healthy, version=APP_VERSION, checks=checks)

    @app.get("/teams", response_model=TeamsOut, summary="Teams in the ownership registry")
    def teams(settings: SettingsDep) -> TeamsOut:
        return TeamsOut(teams=_load_teams(settings))

    @app.post(
        "/runs",
        summary="Run one team and return its scorecard",
        response_description="The scorecard HTML, or a JSON summary for an API caller.",
        responses={
            200: {"content": {"text/html": {}, "application/json": {}}},
            400: {"description": "Unknown team, or a registry the run refused to use"},
            409: {"description": "A run is already in progress"},
        },
    )
    # A plain def, not async: a run is minutes of blocking work, so FastAPI runs it in the
    # thread pool instead of stalling the event loop for every other request.
    def create_run(request: Request, settings: SettingsDep, body: RunRequest) -> Response:
        summary, outputs, out_dir = _execute(settings, body)
        headers = {
            "X-Alerts-BI-Run-Id": summary.run_id,
            "X-Alerts-BI-Team": summary.team_id,
            "X-Alerts-BI-Out-Dir": str(out_dir),
        }
        if _wants_html(request):
            return HTMLResponse(outputs["scorecard.html"], headers=headers)
        return JSONResponse(
            RunOut(
                run_id=summary.run_id,
                team_id=summary.team_id,
                phase=summary.phase,
                phase2_readiness_pct=summary.readiness,
                v1=VolumeOut(rows=summary.v1_rows, distinct=summary.v1_identities),
                v2=VolumeOut(rows=summary.v2_rows, distinct=summary.v2_identities),
                llm_assessed=summary.llm_assessed,
                llm_eligible=summary.llm_eligible,
                out_dir=str(out_dir),
                scorecard=f"/runs/{summary.run_id}",
                files={name: f"/runs/{summary.run_id}/{name}" for name in OUTPUT_FILES},
            ).model_dump(),
            headers=headers,
        )

    @app.get(
        "/runs/{run_id}",
        response_class=HTMLResponse,
        summary="Re-render a stored run's scorecard from SQL",
    )
    def get_scorecard(
        settings: SettingsDep,
        run_id: str,
        team: Annotated[str | None, Query(description='required when run_id is "latest"')] = None,
    ) -> HTMLResponse:
        resolved, outputs = _resolve(settings, run_id, team)
        return HTMLResponse(outputs["scorecard.html"], headers={"X-Alerts-BI-Run-Id": resolved})

    @app.get(
        "/runs/{run_id}/{name}",
        summary="One of the four approved outputs, rendered from SQL",
        responses={200: {"content": {"text/html": {}, "text/csv": {}}}},
    )
    def get_output(
        settings: SettingsDep,
        run_id: str,
        name: OutputFile,
        team: Annotated[str | None, Query(description='required when run_id is "latest"')] = None,
    ) -> Response:
        resolved, outputs = _resolve(settings, run_id, team)
        headers = {"X-Alerts-BI-Run-Id": resolved}
        if name.endswith(".html"):
            return HTMLResponse(outputs[name], headers=headers)
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
        return Response(outputs[name], media_type=CSV_MEDIA_TYPE, headers=headers)

    return app


# ------------------------------------------------------------------------- pages

_PAGE_CSS = """
:root { color-scheme: light dark; --line:#d0d7de; --muted:#57606a; }
body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  margin: 2.5rem auto; max-width: 46rem; padding: 0 1.25rem; line-height: 1.5; }
h1 { font-size: 1.3rem; margin-bottom: .25rem; }
p.sub { color: var(--muted); margin-top: 0; }
form { border: 1px solid var(--line); border-radius: 8px; padding: 1rem 1.25rem; margin: 1.5rem 0; }
label { display: block; font-size: .85rem; margin: .75rem 0 .2rem; }
select, input { font: inherit; padding: .4rem .5rem; width: 100%; box-sizing: border-box;
  border: 1px solid var(--line); border-radius: 6px; background: transparent; color: inherit; }
button { font: inherit; margin-top: 1.1rem; padding: .5rem 1.1rem; border-radius: 6px;
  border: 1px solid var(--line); cursor: pointer; background: transparent; color: inherit; }
code { font-size: .85em; }
table { border-collapse: collapse; width: 100%; font-size: .86rem; margin-top: .5rem; }
th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--line); }
.note { color: var(--muted); font-size: .85rem; }
"""

#: The form posts JSON so the request body matches the documented contract exactly, rather
#: than a second form-encoded shape the endpoint would also have to accept.
_SUBMIT_SCRIPT = """
document.querySelector('form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector('button');
  button.disabled = true;
  button.textContent = 'Running\\u2026';
  const data = Object.fromEntries(new FormData(form).entries());
  if (!data.run_at) delete data.run_at;
  const response = await fetch('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/html' },
    body: JSON.stringify(data),
  });
  document.open();
  document.write(await response.text());
  document.close();
});
"""


def _page(title: str, body: str, script: str = "") -> str:
    tail = f"<script>{script}</script>" if script else ""
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape_html(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body>{body}{tail}</body></html>\n"
    )


def _index_page(teams: list[TeamOut]) -> str:
    options = "\n".join(
        f'<option value="{escape_html(t.team_id)}">'
        f"{escape_html(t.display_name)} ({escape_html(t.team_id)})</option>"
        for t in teams
    )
    rows = "\n".join(
        f"<tr><td><code>{escape_html(t.team_id)}</code></td>"
        f"<td>{escape_html(t.display_name)}</td>"
        f"<td>{len(t.v1_operators)}</td>"
        f"<td>{escape_html(t.v2_operator or '—')}</td>"
        f"<td>{t.panels}</td></tr>"
        for t in teams
    )
    body = f"""
<h1>Alerts BI</h1>
<p class="sub">Start a run for one team. The response is that run's scorecard.</p>
<form method="post" action="/runs">
  <label for="team">Team</label>
  <select id="team" name="team" required>{options}</select>
  <label for="run_at">run_at (UTC, optional)</label>
  <input id="run_at" name="run_at" placeholder="2026-08-25T18:00:00Z">
  <label for="llm">Model</label>
  <select id="llm" name="llm">
    <option value="live">live — the on-prem model, if LLM_ENABLED</option>
    <option value="fake" selected>fake — deterministic client, for the mock</option>
    <option value="off">off — eligible identities become unassessed</option>
  </select>
  <button type="submit">Run</button>
</form>
<p class="note">A run reads 168 hours for one team, so it can take a while; the response
arrives when it finishes. Runs are serialized — a second request while one is running gets
a 409. Against the mock dataset set <code>run_at</code> to
<code>2026-08-25T18:00:00Z</code>, which is the clock it was generated on.</p>
<h2>Registered teams</h2>
<table><thead><tr><th>team_id</th><th>display name</th><th>v1 operators</th>
<th>v2 operator</th><th>panels</th></tr></thead><tbody>{rows}</tbody></table>
<p class="note">Interactive API documentation: <a href="/docs">/docs</a> ·
<a href="/redoc">/redoc</a> · <a href="/openapi.json">openapi.json</a></p>
"""
    return _page("Alerts BI", body, _SUBMIT_SCRIPT)


def _error_page(code: int, detail: str) -> str:
    body = f'<h1>{code}</h1><p>{escape_html(detail)}</p><p class="note"><a href="/">Back</a></p>'
    return _page(str(code), body)


# ------------------------------------------------------------------------ server


def serve(
    config: AppConfig | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    registry_path: str | None = None,
    database: str | None = None,
) -> None:
    """Run the trigger surface until interrupted."""
    settings = Settings(config or load_config(), registry_path, database)
    log.info(
        "api.listening",
        host=host,
        port=port,
        database=settings.database,
        loopback_only=host in ("127.0.0.1", "localhost", "::1"),
    )
    uvicorn.run(build_app(settings), host=host, port=port, log_level="warning")
