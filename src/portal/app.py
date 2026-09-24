"""The reader portal's application factory (design section 7.10).

A separate FastAPI application from the trigger surface of section 7.9, sharing no router,
no listener and no credential with it. Three guarantees are enforced here rather than hoped
for:

* **GET only.** Every route is a GET, and a middleware answers any other method with 405
  before routing. There is no form that posts and nothing that writes.
* **Company network only.** A client whose address is not on the configured allowlist gets
  403. There are no viewer logins.
* **Nothing executes in the browser.** The pages carry no script, and the
  Content-Security-Policy forbids script, framing, plugins and inline styles.

Every page is rendered from the ``portal_*`` views over the portal's own read-only SQL
login; nothing here reaches Elasticsearch, the model, or the run pipeline.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from src.config import PortalSettings
from src.config.portal import IpNetwork
from src.db.connection import Database, connect
from src.logging_setup import log, redact_error
from src.portal import pages, queries
from src.portal.assets import STYLESHEET, STYLESHEET_PATH
from src.versions import APP_VERSION

__all__ = ["READ_METHODS", "SECURITY_HEADERS", "build_portal", "client_allowed"]

READ_METHODS = frozenset({"GET", "HEAD"})

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


# Query parameters, declared at module level so FastAPI can resolve the annotations.
Show = Annotated[str, Query(pattern="^(attention|all)$")]
SchemaFilter = Annotated[str, Query(pattern="^(all|v1|v2)$")]
Page = Annotated[int, Query(ge=1, le=100_000)]


def client_allowed(host: str | None, networks: tuple[IpNetwork, ...]) -> bool:
    """Is a client address on the allowlist? Anything that is not an IP address is not."""
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in networks)


def build_portal(settings: PortalSettings) -> FastAPI:
    app = FastAPI(
        title="Alerts BI review portal",
        version=APP_VERSION,
        # No interactive docs and no OpenAPI document: the portal has no API to explore, and
        # the docs page would be the one place that loads a script.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    networks = settings.networks()

    @contextmanager
    def database() -> Iterator[Database]:
        try:
            with connect(settings.sql, settings.database) as db:
                yield db
        except HTTPException:
            raise
        except Exception as exc:
            log.error("portal.database_unavailable", error=redact_error(exc))
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "The review data is unavailable right now."
            ) from None

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        client = request.client.host if request.client else None
        if not client_allowed(client, networks):
            log.info("portal.client_refused", client=client)
            response: Response = PlainTextResponse("Forbidden", status_code=403)
        elif request.method not in READ_METHODS:
            response = PlainTextResponse(
                "This portal is read-only.",
                status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
                headers={"Allow": "GET, HEAD"},
            )
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException) -> Response:
        if request.url.path == "/healthz":
            return JSONResponse({"ok": False, "detail": exc.detail}, status_code=exc.status_code)
        return HTMLResponse(pages.error_page(exc.status_code, str(exc.detail)), exc.status_code)

    @app.get(STYLESHEET_PATH, include_in_schema=False)
    def stylesheet() -> Response:
        return Response(
            STYLESHEET,
            media_type="text/css; charset=utf-8",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        with database() as db:
            db.query("SELECT TOP 1 run_id FROM portal_reviews")
        return JSONResponse({"ok": True, "version": APP_VERSION})

    @app.get("/", response_class=HTMLResponse)
    def directory() -> HTMLResponse:
        with database() as db:
            teams = queries.list_teams(db)
        return HTMLResponse(pages.directory_page(teams))

    def _reviews(db: Database, team_id: str) -> list[queries.Review]:
        reviews = queries.team_reviews(db, team_id)
        if not reviews:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No published review for this team.")
        return reviews

    def _week(reviews: list[queries.Review], week: str) -> queries.Review:
        try:
            wanted = date.fromisoformat(week)
        except ValueError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "No published review for that week."
            ) from None
        for review in reviews:
            if review.week == wanted:
                return review
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No published review for that week.")

    def _team_response(
        db: Database,
        reviews: list[queries.Review],
        selected: queries.Review,
        show: str,
        schema: str,
        page: int,
    ) -> HTMLResponse:
        attention_only = show != "all"
        schema_filter = schema if schema in ("v1", "v2") else None
        listing = queries.worklist(
            db,
            selected.run_id,
            attention_only=attention_only,
            schema=schema_filter,
            page=page,
            page_size=settings.page_size,
        )
        in_scope = [
            totals
            for name, totals in selected.totals.items()
            if schema_filter is None or name == schema_filter
        ]
        counts = {
            "attention": sum(t.needs_attention for t in in_scope),
            "all": sum(t.distinct_alerts for t in in_scope),
        }
        decided = queries.latest_decisions(db, selected.run_id, listing.rows)
        return HTMLResponse(
            pages.team_page(
                reviews,
                selected,
                listing,
                decided,
                show="all" if not attention_only else "attention",
                schema=schema_filter or "all",
                counts=counts,
            )
        )

    @app.get("/teams/{team_id}", response_class=HTMLResponse)
    def team_latest(
        team_id: str, show: Show = "attention", schema: SchemaFilter = "all", page: Page = 1
    ) -> HTMLResponse:
        with database() as db:
            reviews = _reviews(db, team_id)
            return _team_response(db, reviews, reviews[-1], show, schema, page)

    @app.get("/teams/{team_id}/weeks")
    def pick_week(team_id: str, week: Annotated[str, Query(max_length=10)]) -> Response:
        """The week picker is a GET form; send it to the week's own address."""
        try:
            chosen = date.fromisoformat(week)
        except ValueError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "No published review for that week."
            ) from None
        return RedirectResponse(pages.week_url(team_id, chosen), status.HTTP_303_SEE_OTHER)

    @app.get("/teams/{team_id}/weeks/{week}", response_class=HTMLResponse)
    def team_week(
        team_id: str,
        week: str,
        show: Show = "attention",
        schema: SchemaFilter = "all",
        page: Page = 1,
    ) -> HTMLResponse:
        with database() as db:
            reviews = _reviews(db, team_id)
            return _team_response(db, reviews, _week(reviews, week), show, schema, page)

    @app.get("/teams/{team_id}/weeks/{week}/alert", response_class=HTMLResponse)
    def alert(
        team_id: str,
        week: str,
        schema: Annotated[str, Query(pattern="^(v1|v2)$")],
        application: Annotated[str, Query(max_length=256)],
        key: Annotated[str, Query(max_length=512)],
    ) -> HTMLResponse:
        with database() as db:
            selected = _week(_reviews(db, team_id), week)
            detail = queries.alert_detail(db, selected.run_id, schema, application, key)
        if detail is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "That alert is not in this week's review."
            )
        return HTMLResponse(pages.alert_page(selected, detail))

    return app
