"""Local HTTP trigger surface.

A convenience wrapper around exactly what the CLI does, so a run can be started from a
browser or a `curl` line instead of a shell in the repository. It adds no analysis of its
own: every endpoint either loads the registry, calls :func:`execute_run` and
:func:`persist_run` with the same arguments the CLI passes, or renders a stored run from
committed SQL rows through :func:`build_run_outputs`. There is no second code path that
could drift from the command line.

Scope discipline is unchanged. A run still names one team and never defaults to all of
them, ``run_at`` is still captured once per run, and the four approved output files are
still written exactly as the CLI writes them.

Built on the standard library's HTTP server rather than a framework. The surface is six
routes with no authentication, no sessions and no content negotiation beyond one header,
and the approved next step - the interactive frontend over persisted runs (design section
7.4) - has not been designed yet. Choosing its framework here would pre-empt that design in
exchange for nothing this file needs.

SECURITY. There is no authentication. Every request triggers real Elasticsearch queries and
real SQL writes, and a run can call the on-prem model. The listener therefore binds to
loopback by default; ``--host`` can widen it, and doing so on a shared machine exposes an
unauthenticated write endpoint to that network.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from alerts_bi.config import AppConfig
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
from alerts_bi.run.orchestrator import execute_run, select_llm_client
from alerts_bi.versions import APP_VERSION

__all__ = ["AlertsBiServer", "build_server", "serve"]

#: How a run may treat the model. The names and their meanings are the CLI's: ``live``
#: matches the CLI's default (which still yields no model when LLM_ENABLED is false),
#: ``fake`` matches ``--fake-llm``, ``off`` matches ``--no-llm``.
LLM_MODES = ("live", "fake", "off")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


class ApiError(Exception):
    """An error with a status code, safe to show a caller."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _parse_run_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"run_at {raw!r} is not a valid ISO 8601 instant"
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _select_client(config: AppConfig, mode: str) -> tuple[LlmClient | None, str | None]:
    if mode not in LLM_MODES:
        raise ApiError(
            HTTPStatus.BAD_REQUEST, f"llm must be one of {', '.join(LLM_MODES)}; got {mode!r}"
        )
    # The deterministic fake stamps its own model_version onto the run record, so a run
    # started this way can never be mistaken for a live one after the fact.
    if mode == "fake":
        return FakeLlmClient(), None
    return select_llm_client(config, use_llm=mode == "live")


class AlertsBiServer(ThreadingHTTPServer):
    """Threading server carrying the configuration its handlers need.

    ``run_lock`` serializes runs. Two concurrent requests for the same team and clock would
    derive the same deterministic ``run_id`` and race to replace each other's rows; two for
    different teams would simply compete for the same Elasticsearch and SQL capacity for no
    gain, since a run reads one team at a time by design.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        config: AppConfig,
        registry_path: str | None = None,
        database: str | None = None,
        out_root: Path | None = None,
    ) -> None:
        super().__init__(address, _Handler)
        self.config = config
        self.registry_path = registry_path
        self.database = database or config.sql.database
        self.out_root = out_root or Path("out")
        self.run_lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    #: Narrowed from BaseHTTPRequestHandler.server so the configuration is typed.
    server: AlertsBiServer

    protocol_version = "HTTP/1.1"
    server_version = f"alerts-bi/{APP_VERSION}"
    sys_version = ""

    # ------------------------------------------------------------------ plumbing

    def log_message(self, format: str, *args: Any) -> None:
        """Route access logs through the structured logger.

        The default writes an Apache-style line straight to stderr, which would put request
        paths outside the redaction the rest of the process goes through.
        """
        log.debug("api.access", client=self.address_string(), message=format % args)

    def _respond(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The scorecard is self-contained and carries no scripts; keep a sniffing browser
        # from deciding otherwise.
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _respond_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._respond(status, json.dumps(payload, indent=2).encode("utf-8"), CONTENT_TYPES[".json"])

    def _respond_error(self, error: ApiError) -> None:
        if self._wants_json():
            self._respond_json(error.status, {"error": error.message, "status": int(error.status)})
            return
        body = _error_page(error).encode("utf-8")
        self._respond(error.status, body, CONTENT_TYPES[".html"])

    def _wants_json(self) -> bool:
        return "application/json" in (self.headers.get("Accept") or "")

    def _read_params(self, query: str) -> dict[str, str]:
        """Merge query-string and body parameters.

        The browser form posts ``application/x-www-form-urlencoded``; a script is more
        likely to send JSON or to put everything in the query string. All three are
        accepted so neither caller has to dress up as the other.
        """
        params = {key: values[-1] for key, values in parse_qs(query).items()}

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return params
        raw = self.rfile.read(length)
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()

        if content_type == "application/json":
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ApiError(HTTPStatus.BAD_REQUEST, "request body is not valid JSON") from None
            if not isinstance(parsed, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "request body must be a JSON object")
            params.update({str(k): str(v) for k, v in parsed.items() if v is not None})
        else:
            params.update(
                {
                    key: values[-1]
                    for key, values in parse_qs(raw.decode("utf-8", "replace")).items()
                }
            )
        return params

    # -------------------------------------------------------------------- routes

    # Method names are fixed by BaseHTTPRequestHandler's dispatch.
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_HEAD(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        segments = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
        try:
            self._route(method, segments, parsed.query)
        except ApiError as error:
            self._respond_error(error)
        except Exception as exc:  # the surface must never leak a traceback
            log.error("api.unhandled", path=parsed.path, error=redact_error(exc))
            self._respond_error(ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, redact_error(exc)))

    def _route(self, method: str, segments: list[str], query: str) -> None:
        if method == "GET" and not segments:
            self._respond(
                HTTPStatus.OK, _index_page(self._teams()).encode("utf-8"), CONTENT_TYPES[".html"]
            )
            return

        if method == "GET" and segments == ["healthz"]:
            self._health()
            return

        if method == "GET" and segments == ["teams"]:
            self._respond_json(HTTPStatus.OK, {"teams": self._teams()})
            return

        if method == "POST" and segments == ["runs"]:
            self._run(self._read_params(query))
            return

        # /runs/<run_id>[/<file>] - <run_id> may be "latest" scoped by ?team=
        if method == "GET" and segments and segments[0] == "runs" and len(segments) in (2, 3):
            name = segments[2] if len(segments) == 3 else "scorecard.html"
            self._report(segments[1], name, query)
            return

        raise ApiError(HTTPStatus.NOT_FOUND, f"no route for {method} /{'/'.join(segments)}")

    # ------------------------------------------------------------------ handlers

    def _teams(self) -> list[dict[str, Any]]:
        path = self.server.registry_path
        try:
            loaded = load_registry(path) if path else load_registry()
        except RegistryError as exc:
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc)) from None
        return [
            {
                "team_id": team.team_id,
                "display_name": team.display_name,
                "v1_operators": list(team.v1_operators),
                "v2_operator": team.v2_operator,
                "panels": len(team.panels),
            }
            for team in loaded.teams
        ]

    def _health(self) -> None:
        config = self.server.config
        checks: dict[str, Any] = {}

        try:
            checks["elasticsearch"] = {"ok": EsClient(config.es).index_exists(V1_INDEX)}
        except Exception as exc:  # report the failure rather than raising
            checks["elasticsearch"] = {"ok": False, "error": redact_error(exc)}

        try:
            with connect(config.sql, self.server.database) as db:
                db.query("SELECT 1 AS ok")
            checks["sql_server"] = {"ok": True, "database": self.server.database}
        except Exception as exc:
            checks["sql_server"] = {"ok": False, "error": redact_error(exc)}

        healthy = all(check["ok"] for check in checks.values())
        self._respond_json(
            HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE,
            {"ok": healthy, "version": APP_VERSION, "checks": checks},
        )

    def _run(self, params: dict[str, str]) -> None:
        team_id = (params.get("team") or params.get("team_id") or "").strip()
        if not team_id:
            # A run never defaults to all teams, so an absent team is an error rather than
            # something to fill in.
            raise ApiError(HTTPStatus.BAD_REQUEST, "team is required; a run never defaults")

        run_at = _parse_run_at(params.get("run_at"))
        client, reason = _select_client(self.server.config, (params.get("llm") or "live").strip())

        if not self.server.run_lock.acquire(blocking=False):
            raise ApiError(
                HTTPStatus.CONFLICT,
                "a run is already in progress; runs are serialized so two cannot race to "
                "write the same rows",
            )
        try:
            summary, outputs, out_dir = self._execute(team_id, run_at, client, reason)
        finally:
            self.server.run_lock.release()

        headers = {
            "X-Alerts-BI-Run-Id": summary.run_id,
            "X-Alerts-BI-Team": summary.team_id,
            "X-Alerts-BI-Out-Dir": str(out_dir),
        }
        if self._wants_json():
            self._respond_json(
                HTTPStatus.OK,
                {
                    "run_id": summary.run_id,
                    "team_id": summary.team_id,
                    "phase": summary.phase,
                    "phase2_readiness_pct": summary.readiness,
                    "v1": {"rows": summary.v1_rows, "distinct": summary.v1_identities},
                    "v2": {"rows": summary.v2_rows, "distinct": summary.v2_identities},
                    "llm_assessed": summary.llm_assessed,
                    "llm_eligible": summary.llm_eligible,
                    "out_dir": str(out_dir),
                    "scorecard": f"/runs/{summary.run_id}",
                    "files": {name: f"/runs/{summary.run_id}/{name}" for name in OUTPUT_FILES},
                },
            )
            return
        self._respond(
            HTTPStatus.OK,
            outputs["scorecard.html"].encode("utf-8"),
            CONTENT_TYPES[".html"],
            headers,
        )

    def _execute(
        self, team_id: str, run_at: datetime, client: LlmClient | None, reason: str | None
    ) -> tuple[Any, dict[str, str], Path]:
        config = self.server.config
        es_client = EsClient(config.es)
        try:
            with connect(config.sql, self.server.database) as db:
                payload, summary = execute_run(
                    team_id=team_id,
                    run_at=run_at,
                    config=config,
                    es_client=es_client,
                    llm_client=client,
                    llm_disabled_reason=reason,
                    registry_path=self.server.registry_path,
                    db=db,
                )
                persist_run(db, payload)
                log.info("run.persisted", run_id=summary.run_id, team_id=summary.team_id)

                # The file contract is the same whether a run is started here or from the
                # command line: the four approved files, written once, under out/.
                out_dir = self.server.out_root / summary.run_id[:16]
                render_run_report(db, summary.run_id, out_dir)
                return summary, build_run_outputs(db, summary.run_id), out_dir
        except RegistryError as exc:
            # An unknown team or a malformed registry entry is the caller's mistake, not a
            # server fault, and the registry is validated before any alert is queried.
            detail = "; ".join([str(exc), *exc.details]) if exc.details else str(exc)
            raise ApiError(HTTPStatus.BAD_REQUEST, detail) from None
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(exc)) from None

    def _report(self, run_id: str, name: str, query: str) -> None:
        if name not in OUTPUT_FILES:
            raise ApiError(
                HTTPStatus.NOT_FOUND,
                f"{name!r} is not one of the four approved outputs: {', '.join(OUTPUT_FILES)}",
            )

        config = self.server.config
        with connect(config.sql, self.server.database) as db:
            resolved = run_id
            if run_id == "latest":
                team = {k: v[-1] for k, v in parse_qs(query).items()}.get("team", "")
                if not team:
                    raise ApiError(HTTPStatus.BAD_REQUEST, "latest requires ?team=<team_id>")
                latest = get_latest_run(db, team)
                if latest is None:
                    raise ApiError(HTTPStatus.NOT_FOUND, f"no completed run stored for team {team}")
                resolved = str(latest["run_id"])

            try:
                outputs = build_run_outputs(db, resolved)
            except ValueError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from None

        suffix = ".html" if name.endswith(".html") else ".csv"
        extra = {"X-Alerts-BI-Run-Id": resolved}
        if suffix == ".csv":
            extra["Content-Disposition"] = f'attachment; filename="{name}"'
        self._respond(HTTPStatus.OK, outputs[name].encode("utf-8"), CONTENT_TYPES[suffix], extra)


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


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape_html(title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body>{body}</body></html>\n"
    )


def _index_page(teams: list[dict[str, Any]]) -> str:
    options = "\n".join(
        f'<option value="{escape_html(t["team_id"])}">'
        f"{escape_html(t['display_name'])} ({escape_html(t['team_id'])})</option>"
        for t in teams
    )
    rows = "\n".join(
        f"<tr><td><code>{escape_html(t['team_id'])}</code></td>"
        f"<td>{escape_html(t['display_name'])}</td>"
        f"<td>{len(t['v1_operators'])}</td>"
        f"<td>{escape_html(t['v2_operator'] or '—')}</td>"
        f"<td>{t['panels']}</td></tr>"
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
<p class="note">Also: <code>GET /teams</code>, <code>GET /healthz</code>,
<code>GET /runs/&lt;run_id&gt;</code>, <code>GET /runs/latest?team=&lt;id&gt;</code>,
and <code>GET /runs/&lt;run_id&gt;/daily_metrics.csv</code>.</p>
"""
    return _page("Alerts BI", body)


def _error_page(error: ApiError) -> str:
    body = (
        f"<h1>{int(error.status)} {escape_html(error.status.phrase)}</h1>"
        f'<p>{escape_html(error.message)}</p><p class="note"><a href="/">Back</a></p>'
    )
    return _page(f"{int(error.status)} {error.status.phrase}", body)


# ------------------------------------------------------------------------ server


def build_server(
    config: AppConfig,
    host: str = "127.0.0.1",
    port: int = 8000,
    registry_path: str | None = None,
    database: str | None = None,
    out_root: Path | None = None,
) -> AlertsBiServer:
    return AlertsBiServer((host, port), config, registry_path, database, out_root)


def serve(
    config: AppConfig,
    host: str = "127.0.0.1",
    port: int = 8000,
    registry_path: str | None = None,
    database: str | None = None,
) -> None:
    """Run the trigger surface until interrupted."""
    server = build_server(config, host, port, registry_path, database)
    bound_host, bound_port = server.server_address[:2]
    log.info(
        "api.listening",
        host=str(bound_host),
        port=bound_port,
        database=server.database,
        loopback_only=host in ("127.0.0.1", "localhost", "::1"),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("api.stopping")
    finally:
        server.server_close()
