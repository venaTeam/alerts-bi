"""The day buckets the slides' charts read: built from view rows, ordered by schema then day."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.insights import SummaryInputs
from src.portal.summary_queries import daily_points


def row(schema: str, day: int, hours: str = "24.000", flagged: int = 1) -> dict[str, object]:
    return {
        "alert_schema": schema,
        "snapshot_date": date(2026, 9, day),
        "covered_hours": Decimal(hours),
        "distinct_alerts": day,
        "flagged_by_rule_distinct": flagged,
    }


def test_day_buckets_are_ordered_by_schema_then_day_whatever_the_row_order() -> None:
    points = daily_points([row("v2", 22), row("v1", 22), row("v2", 21), row("v1", 21, "6.500")])
    assert [(p.alert_schema, p.day.day) for p in points] == [
        ("v1", 21),
        ("v1", 22),
        ("v2", 21),
        ("v2", 22),
    ]


def test_each_bucket_carries_its_hours_and_both_counts() -> None:
    (point,) = daily_points([row("v1", 21, "6.500", flagged=4)])
    assert point.covered_hours == 6.5 and isinstance(point.covered_hours, float)
    assert point.distinct_alerts == 21 and point.rule_flagged_distinct == 4
    assert point.day == date(2026, 9, 21)


def test_no_rows_means_no_buckets_and_inputs_default_to_none() -> None:
    assert daily_points([]) == ()
    assert SummaryInputs.__dataclass_fields__["daily"].default == ()
