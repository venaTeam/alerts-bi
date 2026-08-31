"""Running the surface under uvicorn."""

from __future__ import annotations

import uvicorn

from src.api.app import build_app
from src.config import ApiSettings
from src.logging_setup import log

__all__ = ["serve"]


def serve(settings: ApiSettings) -> None:
    """Run the trigger surface until interrupted."""
    log.info(
        "api.listening",
        host=settings.host,
        port=settings.port,
        database=settings.target_database,
        loopback_only=settings.loopback_only,
    )
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, log_level="warning")
