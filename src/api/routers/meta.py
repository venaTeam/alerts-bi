"""Discovery: what this instance is, what it can run, and whether it is healthy."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from fastapi.responses import HTMLResponse

from src.api.dependencies import SettingsDep
from src.api.schemas import HealthOut, TeamsOut
from src.api.service import check_health, list_teams
from src.api.ui import index_page
from src.versions import APP_VERSION

__all__ = ["router"]

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(settings: SettingsDep) -> HTMLResponse:
    return HTMLResponse(index_page(list_teams(settings)))


@router.get("/healthz", response_model=HealthOut, summary="Liveness and dependency checks")
def healthz(settings: SettingsDep, response: Response) -> HealthOut:
    checks = check_health(settings)
    healthy = all(check.ok for check in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(ok=healthy, version=APP_VERSION, checks=checks)


@router.get("/teams", response_model=TeamsOut, summary="Teams in the ownership registry")
def teams(settings: SettingsDep) -> TeamsOut:
    return TeamsOut(teams=list_teams(settings))
