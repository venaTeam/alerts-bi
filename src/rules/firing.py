"""R6: one alert's firing episodes (design 7.14).

Grafana's repeat interval is disabled on both schemas, so a still-firing alert sends once
when it fires and once when it clears. R6 therefore judges *episodes*, never a cadence.

An *episode* is a maximal run of consecutive firing (non-clear) rows, ordered by
``(timestamp, doc_hash)``; a clear row closes it. The firing rows after the last clear form
the *open episode*.

Known limitation: an alert that started firing before the window and never sent again has
no rows in the window, so it is invisible to that week.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.domain.normalize import AlertRecord
from src.rules.catalogs import (
    R6_API_MIN_SPAN,
    R6_API_SPAM_PER_24H,
    R6_FLAP_CYCLES,
    R6_FLAP_WINDOW,
    R6_SPAM_EPISODE_ROWS,
    R6_STUCK_OPEN,
)

__all__ = ["FiringFacts", "firing_facts", "is_clear"]

_DAY = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class FiringFacts:
    clear_count: int
    max_clear_cycles_24h: int
    max_episode_firing_rows: int
    open_since: datetime | None
    """First firing row of the open episode, when the last row is firing."""
    span: timedelta
    """``last - first``; zero for one row."""
    events_per_24h: float | None
    """Display only; None when the span is under the API minimum."""
    pattern: str | None


def is_clear(row: AlertRecord) -> bool:
    """v1 clears with severity ``clear`` (code 1); v2 with ``status = resolved``."""
    if row.schema == "v1":
        return row.severity == "clear"
    return row.status == "resolved"


def firing_facts(
    schema: str, rows: Sequence[AlertRecord], provider: str | None, window_end: datetime
) -> FiringFacts:
    """Facts and R6 pattern for one identity's rows (``rows`` must not be empty).

    ``provider`` is the representative row's provider; ``window_end`` is the run's exclusive
    window end. Thresholds use exact ``timedelta`` arithmetic.
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

    max_episode = 0
    current = 0
    episode_start: datetime | None = None
    for row, clear in zip(ordered, clears, strict=True):
        if clear:
            current = 0
            episode_start = None
            continue
        if current == 0:
            episode_start = row.timestamp
        current += 1
        max_episode = max(max_episode, current)
    open_since = episode_start if current > 0 else None

    n = len(ordered)
    span = ordered[-1].timestamp - ordered[0].timestamp
    grafana = provider == "grafana"
    events_per_24h = n / (span / _DAY) if span >= R6_API_MIN_SPAN else None
    clear_count = sum(clears)

    pattern: str | None = None
    if max_cycles >= R6_FLAP_CYCLES:
        pattern = "flapping"
    elif (grafana and max_episode >= R6_SPAM_EPISODE_ROWS) or (
        not grafana and span >= R6_API_MIN_SPAN and n * _DAY >= R6_API_SPAM_PER_24H * span
    ):
        pattern = "spamming"
    elif grafana and open_since is not None and window_end - open_since >= R6_STUCK_OPEN:
        pattern = "stuck"
    return FiringFacts(
        clear_count, max_cycles, max_episode, open_since, span, events_per_24h, pattern
    )
