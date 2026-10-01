"""Per-application rows, fire-frequency rows and the biggest single source (spec section 4)."""

from __future__ import annotations

from src.insights.model import AlertRow, AppRow, FireRow


def by_application(alerts: tuple[AlertRow, ...]) -> tuple[AppRow, ...]:
    raise NotImplementedError


def fire_rows(alerts: tuple[AlertRow, ...]) -> tuple[FireRow, ...]:
    raise NotImplementedError


def biggest(alerts: tuple[AlertRow, ...]) -> AlertRow | None:
    raise NotImplementedError
