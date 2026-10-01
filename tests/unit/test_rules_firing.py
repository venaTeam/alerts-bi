"""R6: stuck, spamming and flapping (team summary spec section 5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from src.domain.normalize import AlertRecord
from src.rules.engine import compute_daily_rule_counts, evaluate_rows
from src.rules.firing import firing_facts, is_clear

from tests.helpers.rows import v1_row, v2_row

T0 = datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
FIVE = timedelta(minutes=5)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _v1(times: list[datetime], clears: set[int] | None = None, **kw: Any) -> list[AlertRecord]:
    clears = clears or set()
    return [
        v1_row(**{"@timestamp": _iso(t), "severity": 1 if i in clears else 5, **kw})
        for i, t in enumerate(times)
    ]


def _every(start: datetime, end: datetime, step: timedelta) -> list[datetime]:
    out = []
    t = start
    while t <= end:
        out.append(t)
        t += step
    return out


def _even(n: int, length: timedelta) -> list[datetime]:
    """``n`` rows from T0 to T0 + length, evenly spaced to the second."""
    secs = int(length.total_seconds())
    return [T0 + timedelta(seconds=round(i * secs / (n - 1))) for i in range(n)]


def test_single_row_spans_one_interval_without_dividing_by_zero() -> None:
    facts = firing_facts("v1", _v1([T0]))
    assert facts.span == FIVE
    assert facts.ratio == 1.0
    assert facts.pattern is None


def test_rows_sharing_one_timestamp_are_spamming() -> None:
    facts = firing_facts("v1", _v1([T0, T0, T0]))
    assert facts.span == FIVE
    assert facts.ratio == 3.0
    assert facts.pattern == "spamming"


def test_stuck_at_exactly_72h_of_span_plus_interval() -> None:
    facts = firing_facts("v1", _v1(_every(T0, T0 + timedelta(hours=72), FIVE)))
    assert facts.span == timedelta(hours=72, minutes=5)
    assert facts.ratio == 1.0
    assert facts.pattern == "stuck"


def test_72h_span_is_inclusive() -> None:
    facts = firing_facts("v1", _v1(_every(T0, T0 + timedelta(hours=71, minutes=55), FIVE)))
    assert facts.span == timedelta(hours=72)
    assert facts.pattern == "stuck"


def test_under_72h_is_not_stuck() -> None:
    facts = firing_facts("v1", _v1(_every(T0, T0 + timedelta(hours=71), FIVE)))
    assert facts.span == timedelta(hours=71, minutes=5)
    assert facts.pattern is None


def test_a_clear_row_prevents_stuck_and_counts_one_cycle() -> None:
    times = _every(T0, T0 + timedelta(hours=72), FIVE)
    facts = firing_facts("v1", _v1(times, clears={10}))
    assert facts.clear_count == 1
    assert facts.max_clear_cycles_24h == 1
    assert facts.pattern is None


def test_grafana_ratio_just_under_two_is_not_spamming() -> None:
    facts = firing_facts("v1", _v1(_even(575, timedelta(hours=24))))
    assert facts.span == timedelta(hours=24, minutes=5)
    assert facts.ratio is not None and facts.ratio < 2.0
    assert facts.pattern is None


def test_grafana_ratio_of_exactly_two_is_spamming() -> None:
    facts = firing_facts("v1", _v1(_even(578, timedelta(hours=24))))
    assert facts.ratio == 2.0
    assert facts.pattern == "spamming"


def test_grafana_ratio_below_point_nine_is_not_stuck() -> None:
    length = timedelta(hours=79, minutes=55)
    facts = firing_facts("v1", _v1(_even(854, length)))
    assert facts.span == timedelta(hours=80)
    assert facts.pattern is None


def test_grafana_ratio_of_exactly_point_nine_is_stuck() -> None:
    length = timedelta(hours=79, minutes=55)
    facts = firing_facts("v1", _v1(_even(864, length)))
    assert facts.ratio == 0.9
    assert facts.pattern == "stuck"


def test_api_alert_at_24_events_per_day_is_spamming() -> None:
    length = timedelta(hours=23, minutes=55)
    facts = firing_facts("v1", _v1(_even(24, length), provider="api", alert_rule_url=None))
    assert facts.span == timedelta(hours=24)
    assert facts.ratio is None
    assert facts.pattern == "spamming"


def test_api_alert_below_24_events_per_day_is_not_spamming() -> None:
    length = timedelta(hours=23, minutes=55)
    facts = firing_facts("v1", _v1(_even(23, length), provider="api", alert_rule_url=None))
    assert facts.pattern is None


def test_api_alert_needs_six_hours_of_span() -> None:
    facts = firing_facts(
        "v1", _v1(_even(30, timedelta(hours=5)), provider="api", alert_rule_url=None)
    )
    assert facts.span < timedelta(hours=6)
    assert facts.pattern is None


def _cycles(clear_hours: list[float]) -> list[AlertRecord]:
    """A fire row one hour before each clear row."""
    times: list[datetime] = []
    clears: set[int] = set()
    for h in clear_hours:
        times.append(T0 + timedelta(hours=h - 1))
        clears.add(len(times))
        times.append(T0 + timedelta(hours=h))
    return _v1(times, clears=clears)


def test_three_cycles_within_24h_is_flapping() -> None:
    facts = firing_facts("v1", _cycles([2, 8, 20]))
    assert facts.max_clear_cycles_24h == 3
    assert facts.pattern == "flapping"


def test_cycles_spread_over_more_than_24h_are_not_flapping() -> None:
    facts = firing_facts("v1", _cycles([2, 14, 27]))
    assert facts.max_clear_cycles_24h == 2
    assert facts.pattern is None


def test_consecutive_clear_rows_are_one_cycle() -> None:
    times = [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2)]
    facts = firing_facts("v1", _v1(times, clears={1, 2}))
    assert facts.clear_count == 2
    assert facts.max_clear_cycles_24h == 1


def test_v2_flapping_reads_status_resolved() -> None:
    times = [T0 + timedelta(hours=h) for h in range(6)]
    rows = [
        v2_row(**{"@timestamp": _iso(t), "status": "resolved" if i % 2 else "firing"})
        for i, t in enumerate(times)
    ]
    assert is_clear(rows[1]) and not is_clear(rows[0])
    facts = firing_facts("v2", rows)
    assert facts.max_clear_cycles_24h == 3
    assert facts.pattern == "flapping"


def test_flapping_outranks_spamming() -> None:
    times = [T0 + timedelta(minutes=i) for i in range(12)]
    facts = firing_facts("v1", _v1(times, clears={1, 3, 5}))
    assert facts.ratio is not None and facts.ratio >= 2.0
    assert facts.pattern == "flapping"


def test_row_order_does_not_change_the_facts() -> None:
    times = [T0, T0, T0 + timedelta(hours=1), T0 + timedelta(hours=1), T0 + timedelta(hours=2)]
    rows = _v1(times, clears={2})
    assert firing_facts("v1", rows) == firing_facts("v1", list(reversed(rows)))


def test_engine_attaches_r6_to_every_row_and_withholds_from_llm() -> None:
    rows = _v1(_every(T0, T0 + timedelta(hours=72), FIVE))
    evaluation = evaluate_rows(rows)
    (identity,) = evaluation.identities.values()
    assert identity.core_rule_ids == ["R6"]
    assert identity.fire_pattern == "stuck"
    assert identity.clear_count == 0
    assert identity.llm_eligible is False
    assert all([f.rule_id for f in item.core_findings] == ["R6"] for item in evaluation.rows)
    dates = sorted({r.snapshot_date for r in rows})
    counts = [c for c in compute_daily_rule_counts(evaluation.rows, dates) if c.rule_id == "R6"]
    assert {c.snapshot_date for c in counts} == set(dates)
    assert all(c.distinct_count == 1 for c in counts)
