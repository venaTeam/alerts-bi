"""Running the reader portal under uvicorn, after proving its credential cannot write."""

from __future__ import annotations

import uvicorn

from src.config import PortalSettings
from src.db.connection import connect
from src.db.reader import read_only_problems
from src.logging_setup import log
from src.portal.app import build_portal

__all__ = ["PortalRefused", "check_credential", "serve"]


class PortalRefused(RuntimeError):
    """The portal's SQL login is more than a reader of the portal views."""


def check_credential(settings: PortalSettings) -> None:
    """Refuse to serve unless the configured login is read-only and can read the views."""
    with connect(settings.sql, settings.database) as db:
        problems = read_only_problems(db)
    if problems:
        raise PortalRefused(
            "the portal's SQL login is not a read-only reader of the portal views:\n  "
            + "\n  ".join(problems)
            + "\nGive it its own login with `alerts-bi db grant-reader` and set "
            "PORTAL_SQL_USER / PORTAL_SQL_PASSWORD."
        )


def serve(settings: PortalSettings) -> None:
    """Check the credential, then serve until interrupted."""
    check_credential(settings)
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
