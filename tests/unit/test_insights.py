"""The team summary's pure insights: estimate, aggregations and key findings."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.insights.aggregate import biggest, by_application, fire_rows
from src.insights.estimate import estimate, v1_rule_key
from src.insights.findings import key_findings
from src.insights.model import (
    AlertRow,
    RuleTotal,
    SchemaTotals,
    SummaryInputs,
    WeekRules,
)
from src.insights.summary import summarize

T0 = datetime(2026, 9, 7, tzinfo=UTC)
FORBIDDEN = ("per day", "run_id", "registry", "ruleset", "prompt", "model version")


def alert(**kw: Any) -> AlertRow:
    base: dict[str, Any] = {
        "schema": "v1",
        "application": "app",
        "key_field": "k",
        "message": "m",
        "severity": "error",
        "provider": "grafana",
        "alert_rule_url": None,
        "component": "c",
        "node_name": None,
        "row_count": 1,
        "first_seen": T0,
        "last_seen": T0,
        "quality_state": "assessed_good",
        "core_rule_ids": (),
        "readiness_rule_ids": (),
        "llm_principle_id": None,
        "llm_confidence": None,
        "clear_count": 0,
        "max_clear_cycles_24h": 0,
        "fire_pattern": None,
        "unseen": None,
    }
    base.update(kw)
    return AlertRow(**base)


def totals(schema: str, **kw: Any) -> SchemaTotals:
    base: dict[str, Any] = {
        "schema": schema,
        "events": 0,
        "distinct_alerts": 0,
        "distinct_per_day": None,
        "rule_flagged_events": 0,
        "rule_flagged_alerts": 0,
        "suppressed": 0,
        "unseen": None,
        "unseen_alerts": None,
        "states": {},
        "readiness_gaps": 0,
    }
    base.update(kw)
    return SchemaTotals(**base)


def inputs(**kw: Any) -> SummaryInputs:
    base: dict[str, Any] = {
        "surface": "portal",
        "team_id": "t",
        "display_name": "T",
        "window_start": T0 - timedelta(days=7),
        "window_end": T0,
        "phase": "phase_1",
        "phase2_readiness_pct": None,
        "schemas": {"v1": totals("v1"), "v2": totals("v2")},
        "rules": (),
        "alerts": (),
        "published": True,
        "history": (),
        "v1_rule_effort_days": None,
    }
    base.update(kw)
    return SummaryInputs(**base)


def week(n: int, rules: set[str], basis_changed: bool = False) -> WeekRules:
    return WeekRules(T0 + timedelta(days=7 * n), frozenset(rules), basis_changed)


# --- v1_rule_key ---------------------------------------------------------


def test_rule_key_uses_url_then_application() -> None:
    assert v1_rule_key(alert(alert_rule_url=" http://g/1 ")) == "url:http://g/1"
    assert v1_rule_key(alert(alert_rule_url=None, application="pay")) == "app:pay"
    assert v1_rule_key(alert(alert_rule_url="   ", application="pay")) == "app:pay"


# --- estimate ------------------------------------------------------------


def test_estimate_without_v1_alerts_has_nothing_left() -> None:
    est = estimate(inputs(alerts=(alert(schema="v2"),)))
    assert est.rules_left == 0
    assert est.effort_days == 0
    assert est.effort_weeks == 0


def test_estimate_not_published() -> None:
    alerts = (alert(key_field="a", alert_rule_url="u1"), alert(key_field="b", application="x"))
    est = estimate(inputs(alerts=alerts, published=False, history=()))
    assert est.no_estimate_reason == (
        "This week is not published, so there is no published history to measure a pace from."
    )
    assert est.rules_left == 2
    assert est.effort_days == 1.0
    assert est.effort_weeks == 0.2
    assert not est.effort_is_override
    assert est.projected_week_end is None


def test_estimate_one_week_of_history() -> None:
    est = estimate(inputs(history=(week(0, {"a"}),)))
    assert est.no_estimate_reason == (
        "Needs at least 2 earlier published weeks back to back; found 0."
    )
    assert est.lookback_weeks == 0


def test_estimate_gap_stops_the_lookback() -> None:
    history = (week(0, {"a", "b"}), week(1, {"a"}), week(3, {"a"}))
    est = estimate(inputs(history=history))
    assert est.lookback_weeks == 0
    assert est.no_estimate_reason is not None and "found 0" in est.no_estimate_reason


def test_estimate_basis_change_on_selected_week() -> None:
    history = (week(0, {"a", "b"}), week(1, {"a", "b"}), week(2, {"a"}, basis_changed=True))
    est = estimate(inputs(history=history))
    assert est.lookback_weeks == 0
    assert est.no_estimate_reason is not None and "found 0" in est.no_estimate_reason


def test_estimate_basis_change_midway_limits_lookback() -> None:
    history = (
        week(0, {"a", "b", "c"}),
        week(1, {"a", "b"}, basis_changed=True),
        week(2, {"a", "b"}),
        week(3, {"a"}),
    )
    est = estimate(inputs(history=history))
    assert est.lookback_weeks == 2
    assert est.retired == 1
    assert est.no_estimate_reason is not None and "Fewer than 2" in est.no_estimate_reason


def test_estimate_retired_one_is_too_few() -> None:
    history = (week(0, {"a", "b"}), week(1, {"a", "b"}), week(2, {"a"}))
    est = estimate(inputs(history=history))
    assert est.retired == 1
    assert est.pace_per_week is None
    assert est.no_estimate_reason == (
        "Fewer than 2 v1 alert rules stopped firing over the last 2 weeks, "
        "too few to measure a pace."
    )


def test_estimate_happy_path() -> None:
    history = (
        week(0, {"a", "b", "c", "d"}),
        week(1, {"a", "b", "c"}),
        week(2, {"a", "b"}),
        week(3, {"a"}),
    )
    est = estimate(inputs(alerts=(alert(alert_rule_url="a"),), history=history))
    assert est.lookback_weeks == 3
    assert est.retired == 3
    assert est.pace_per_week == 1.0
    assert est.rules_left == 1
    assert est.no_estimate_reason is None
    assert est.projected_week_end == (history[-1].week_end + timedelta(days=7)).date()


def test_estimate_lookback_is_capped_at_three() -> None:
    history = tuple(week(i, {"a", "b", "c", "d", "e"} if i < 5 else {"a"}) for i in range(6))
    assert estimate(inputs(history=history)).lookback_weeks == 3


def test_estimate_no_rules_left_projects_the_selected_week() -> None:
    history = (week(0, {"a", "b"}), week(1, {"a", "b"}), week(2, set()))
    est = estimate(inputs(alerts=(), history=history))
    assert est.rules_left == 0
    assert est.projected_week_end == history[-1].week_end.date()
    assert isinstance(est.projected_week_end, date)
    assert est.effort_days == 0


def test_estimate_override() -> None:
    alerts = tuple(alert(key_field=str(i), alert_rule_url=f"u{i}") for i in range(4))
    est = estimate(inputs(alerts=alerts, v1_rule_effort_days=2.0))
    assert est.effort_days == 8.0
    assert est.effort_weeks == 1.6
    assert est.effort_is_override
    assert est.effort_days_per_rule == 2.0


# --- aggregations ----------------------------------------------------------


def test_by_application_counts_and_order() -> None:
    alerts = (
        alert(
            application="b",
            key_field="1",
            row_count=10,
            quality_state="rule_flagged",
            core_rule_ids=("R3", "R1"),
        ),
        alert(application="b", key_field="2", row_count=5, quality_state="llm_flagged"),
        alert(application="b", key_field="3", row_count=1),
        alert(application="a", key_field="1", row_count=100),
        alert(
            application="c",
            key_field="1",
            row_count=15,
            quality_state="rule_flagged",
            core_rule_ids=("R2",),
        ),
        alert(application="b", schema="v2", key_field="9", row_count=1),
    )
    rows = by_application(alerts)
    assert [(r.application, r.schema) for r in rows] == [
        ("b", "v1"),
        ("c", "v1"),
        ("a", "v1"),
        ("b", "v2"),
    ]
    b = rows[0]
    assert (b.alerts, b.rule_flagged_alerts, b.llm_flagged_alerts) == (3, 1, 1)
    assert (b.events, b.rule_flagged_events, b.llm_flagged_events) == (16, 10, 5)
    assert b.rules == ("R1", "R3")


def test_fire_rows_single_row_has_one_interval_span() -> None:
    (row,) = fire_rows((alert(row_count=1),))
    assert row.span_hours == 5 / 60
    assert row.ratio == 1.0
    assert row.events_per_24h == 288.0


def test_fire_rows_api_alert_has_no_ratio_and_orders_by_events() -> None:
    api = alert(
        provider="api", key_field="x", row_count=48, last_seen=T0 + timedelta(hours=23, minutes=55)
    )
    small = alert(key_field="y", row_count=2)
    rows = fire_rows((small, api))
    assert [r.alert.key_field for r in rows] == ["x", "y"]
    assert rows[0].ratio is None
    assert rows[0].events_per_24h == 48.0
    assert rows[0].pattern is None
    flagged = fire_rows((alert(fire_pattern="stuck"),))
    assert flagged[0].pattern == "stuck"


def test_biggest_tie_break() -> None:
    a = alert(schema="v2", key_field="a", row_count=9)
    b = alert(schema="v1", key_field="z", row_count=9)
    c = alert(schema="v1", key_field="b", row_count=9)
    assert biggest((a, b, c)) is c
    assert biggest(()) is None


# --- key findings ----------------------------------------------------------


def full_inputs() -> SummaryInputs:
    v1_alerts = tuple(
        alert(key_field=f"k{i}", row_count=c, core_rule_ids=("R1", "R4") if i < 2 else ())
        for i, c in enumerate((900, 50, 20, 20, 10))
    )
    alerts = (
        *v1_alerts,
        alert(schema="v2", key_field="c", severity="critical", readiness_rule_ids=("R9",)),
        alert(schema="v2", key_field="d", severity="warning", readiness_rule_ids=("R9",)),
        alert(schema="v2", key_field="e", severity="critical"),
    )
    return inputs(
        schemas={
            "v1": totals(
                "v1", events=1000, suppressed=7, unseen=3, unseen_alerts=1, states={"unassessed": 2}
            ),
            "v2": totals("v2", events=3, suppressed=2, unseen=1, unseen_alerts=1),
        },
        rules=(
            RuleTotal("v1", "R1", 950, 2),
            RuleTotal("v1", "R4", 950, 2),
            RuleTotal("v2", "R3", 1, 1),
        ),
        alerts=alerts,
    )


def test_key_findings_order_and_cap() -> None:
    findings = key_findings(full_inputs())
    assert [f.kind for f in findings] == [
        "largest",
        "unassessed",
        "concentration",
        "hidden",
        "unseen",
    ]
    assert len(findings) == 5


def test_largest_and_co_occurrence() -> None:
    f = key_findings(full_inputs())[0]
    assert f.title == "R1 is your largest finding"
    assert f.body == "950 v1 events from 2 alerts. The same alerts also match R4."
    assert f.rule_filter == "R1"
    assert f.fix is None


def test_largest_without_co_occurrence() -> None:
    alerts = (alert(core_rule_ids=("R1",), row_count=3),)
    f = key_findings(inputs(alerts=alerts, rules=(RuleTotal("v1", "R1", 3, 1),)))[0]
    assert f.body == "3 v1 events from 1 alert."


def test_unassessed_text() -> None:
    f = key_findings(full_inputs())[1]
    assert f.title == "2 alerts could not be classified"
    assert f.body.startswith("Unassessed should be zero.")


def test_concentration_threshold() -> None:
    f = next(x for x in key_findings(full_inputs()) if x.kind == "concentration")
    assert f.title == "1 of 5 v1 alerts make 90% of the events"
    assert f.body == "They produced 900 of 1,000 v1 events this week."
    # Two alerts, or an even spread, produce nothing.
    two = inputs(
        alerts=(alert(key_field="a", row_count=99), alert(key_field="b")),
        schemas={"v1": totals("v1", events=100), "v2": totals("v2")},
    )
    assert all(x.kind != "concentration" for x in key_findings(two))
    flat = tuple(alert(key_field=str(i), row_count=10) for i in range(5))
    flat_in = inputs(alerts=flat, schemas={"v1": totals("v1", events=50), "v2": totals("v2")})
    assert next(x for x in key_findings(flat_in) if x.kind == "concentration").title == (
        "4 of 5 v1 alerts make 80% of the events"
    )
    one = tuple(alert(key_field=str(i), row_count=10) for i in range(3))
    # k == n would mean no concentration: 3 equal alerts need all 3 for 80%? 2 of 3 = 67%.
    one_in = inputs(alerts=one, schemas={"v1": totals("v1", events=30), "v2": totals("v2")})
    assert all(x.kind != "concentration" for x in key_findings(one_in))


def test_hidden_omitted_when_nothing_suppressed() -> None:
    base = full_inputs()
    clean = replace(base, schemas={"v1": totals("v1", events=1000), "v2": totals("v2")})
    assert all(f.kind != "hidden" for f in key_findings(clean))
    hidden = next(f for f in key_findings(base) if f.kind == "hidden")
    assert hidden.body == "7 v1 events and 2 v2 events match a filter in your dashboard (R5)."
    assert hidden.rule_filter == "R5"


def test_unseen_omitted_when_none_and_present_otherwise() -> None:
    base = full_inputs()
    none = replace(base, schemas={"v1": totals("v1"), "v2": totals("v2")})
    assert all(f.kind != "unseen" for f in key_findings(none))
    f = next(x for x in key_findings(base) if x.kind == "unseen")
    assert f.body == "2 alerts (4 events) are outside every panel's narrowing."
    assert f.fix == "Widen a panel to include them, or confirm they are meant to stay out of view."


def test_readiness_critical_count() -> None:
    base = full_inputs()
    only = replace(base, schemas={"v1": totals("v1"), "v2": totals("v2")}, rules=())
    f = next(x for x in key_findings(only) if x.kind == "readiness")
    assert f.title == "2 of 3 v2 alerts are phase-2 ready"
    assert f.body == "1 critical alert has no runbook."
    assert f.fix == "Add impact and an https:// runbook, starting with critical alerts."
    ok = replace(only, alerts=(alert(schema="v2", severity="warning", readiness_rule_ids=("R9",)),))
    f = key_findings(ok)[0]
    assert f.title == "1 of 1 v2 alerts are phase-2 ready"
    assert f.body == "Every critical alert has a runbook."
    r8 = replace(only, alerts=(alert(schema="v2", readiness_rule_ids=("R8",)),))
    assert key_findings(r8)[0].title == "0 of 1 v2 alerts are phase-2 ready"


def test_no_findings_when_nothing_to_say() -> None:
    assert key_findings(inputs()) == ()


def test_generated_strings_are_free_of_internals() -> None:
    base = full_inputs()
    only = replace(base, schemas={"v1": totals("v1"), "v2": totals("v2")})
    for source in (base, only, inputs()):
        for f in key_findings(source):
            for text in (f.title, f.body, f.fix or ""):
                assert not any(word in text.lower() for word in FORBIDDEN), text
    est = estimate(inputs(history=(week(0, {"a"}),)))
    assert est.no_estimate_reason is not None
    assert not any(w in est.no_estimate_reason.lower() for w in FORBIDDEN)


def test_summarize_assembles_everything() -> None:
    source = full_inputs()
    summary = summarize(source)
    assert summary.inputs is source
    assert summary.key_findings == key_findings(source)
    assert summary.by_application == by_application(source.alerts)
    assert summary.fire == fire_rows(source.alerts)
    assert summary.biggest == biggest(source.alerts)
    assert summary.estimate == estimate(source)
