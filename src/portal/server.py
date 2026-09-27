"""Run the portal under uvicorn."""

from __future__ import annotations

import uvicorn

from src.config import PortalSettings
from src.logging_setup import log
from src.portal.app import build_portal

__all__ = ["serve"]


def serve(settings: PortalSettings) -> None:
    """Serve until interrupted; view queries use the configured SQL connection."""
    log.info(
        "portal.listening",
        host=settings.host,
        port=settings.port,
        database=settings.database,
        login=settings.sql.user,
        allowed_networks=list(settings.allowed_networks),
    )
    uvicorn.run(
        build_portal(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",
        server_header=False,
    )
