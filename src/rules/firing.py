"""R6: one alert's firing pattern against its schema's repeat interval (team summary spec 5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from fractions import Fraction

from src.domain.cadence import REPEAT_INTERVAL
from src.domain.normalize import AlertRecord
from src.rules.catalogs import (
    R6_API_MIN_SPAN,
    R6_API_SPAM_PER_24H,
    R6_FLAP_CYCLES,
    R6_FLAP_WINDOW,
    R6_SPAM_RATIO,
    R6_STUCK_MIN_SPAN,
    R6_STUCK_RATIO,
)

__all__ = ["FiringFacts", "firing_facts", "is_clear"]

_DAY = timedelta(hours=24)
_US = timedelta(microseconds=1)


def _at_least(events_x_unit: timedelta, threshold: float, span: timedelta) -> bool:
    """Exact ``events_x_unit >= threshold * span`` (threshold is a short decimal)."""
    t = Fraction(str(threshold))
    return Fraction(events_x_unit // _US) >= t * (span // _US)


@dataclass(frozen=True, slots=True)
class FiringFacts:
    clear_count: int
    max_clear_cycles_24h: int
    span: timedelta
    ratio: float | None
    events_per_24h: float
    pattern: str | None


def is_clear(row: AlertRecord) -> bool:
    """v1 clears with severity ``clear`` (code 1); v2 with ``status = resolved``."""
    if row.schema == "v1":
        return row.severity == "clear"
    return row.status == "resolved"


def firing_facts(schema: str, rows: Sequence[AlertRecord], provider: str | None) -> FiringFacts:
    """Facts and R6 pattern for one identity's rows (``rows`` must not be empty).

    ``provider`` is the identity's representative row's provider. Thresholds are decided
    with exact ``timedelta`` arithmetic; floats are kept only for evidence and display.
    """
    ordered = sorted(rows, key=lambda r: (r.timestamp, r.doc_hash))
    clears = [is_clear(r) for r in ordered]
    cycle_times = [
        ordered[i].timestamp for i in range(1, len(ordered)) if clears[i] and not clears[i - 1]
    ]
    max_cycles = 0
    start = 0
    for end, t in enumerate(cycle_times):
        while t - cycle_times[start] >= R6_FLAP_WINDOW:
            start += 1
        max_cycles = max(max_cycles, end - start + 1)
    interval = REPEAT_INTERVAL[schema]
    span = ordered[-1].timestamp - ordered[0].timestamp + interval
    n = len(ordered)
    grafana = provider == "grafana"
    ratio = n / (span / interval) if grafana else None
    per_24h = n / (span / _DAY)
    clear_count = sum(clears)
    expected = n * interval  # rows x interval, compared against the span exactly
    pattern: str | None = None
    if max_cycles >= R6_FLAP_CYCLES:
        pattern = "flapping"
    elif (grafana and _at_least(expected, R6_SPAM_RATIO, span)) or (
        not grafana and _at_least(n * _DAY, R6_API_SPAM_PER_24H, span) and span >= R6_API_MIN_SPAN
    ):
        pattern = "spamming"
    elif (
        grafana
        and _at_least(expected, R6_STUCK_RATIO, span)
        and span >= R6_STUCK_MIN_SPAN
        and clear_count == 0
    ):
        pattern = "stuck"
    return FiringFacts(clear_count, max_cycles, span, ratio, per_24h, pattern)
