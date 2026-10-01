"""The admin Summary page's mapping and admin-only widgets, without a database (spec 4, 7)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import pytest
from src.admin.summary import (
    AdminSummary,
    Publication,
    WorklistFilter,
    alert_row,
    basis_changes,
    day_by_day,
    highlight_panel_sql,
    read_snapshot,
    rule_totals,
    schema_totals,
    summary_inputs,
    summary_page,
    worklist_where,
)

END = datetime(2026, 8, 24)
START = END - timedelta(hours=168)


def finding(**overrides: Any) -> dict[str, Any]:
    return {
        "alert_schema": "v1",
        "application": "checkout-api",
        "key_field": "checkout-api:cart:node-1",
        "message": "Cart service error rate above 2%",
        "severity": "error",
        "provider": "grafana",
        "alert_rule_url": "https://grafana.internal/d/checkout-1",
        "component": "cart",
        "node_name": "node-1",
        "row_count": 4,
        "first_seen": datetime(2026, 8, 20, 9),
        "last_seen": datetime(2026, 8, 20, 12),
        "quality_state": "assessed_good",
        "core_rule_ids": "",
        "readiness_rule_ids": "",
        "llm_principle_id": "NONE",
        "llm_confidence": "high",
        "clear_count": 0,
        "max_clear_cycles_24h": 0,
        "fire_pattern": None,
        "unseen": None,
        **overrides,
    }


def daily(day: int, **overrides: Any) -> dict[str, Any]:
    start = datetime(2026, 8, day)
    return {
        "alert_schema": "v1",
        "snapshot_date": start.date(),
        "bucket_start": start,
        "bucket_end": start + timedelta(days=1),
        "covered_hours": 24,
        "alerts": 10,
        "distinct_alerts": 3,
        "flagged_by_rule": 2,
        "suppressed": 1,
        "suppression_unmeasured": 0,
        "unseen": None,
        "unseen_unmeasured": None,
        **overrides,
    }


def run(**overrides: Any) -> dict[str, Any]:
    return {
        "run_id": "1" * 64,
        "team_id": "checkout-api",
        "team_display_name": "Checkout API",
        "run_at": END,
        "window_start": START,
        "window_end": END,
        "status": "completed",
        "phase_derived": "phase_1",
        "phase2_readiness_pct": 50,
        "registry_version": "2026-08-30.1",
        "ruleset_version": "1.1.0",
        "prompt_version": "1.2.0",
        "model_version": "fake-model-1",
        "completed_at": END,
        "registry_entry_snapshot": (
            '{"team_id":"checkout-api","v1_operators":["checkout","CHECKOUT"],'
            '"v2_operator":"checkout-v2","planning":{"v1_rule_effort_days":2},'
            '"panels":[{"panel_id":"main","schema":"v1",'
            '"sql":"SELECT * FROM alerts WHERE node_name != \'junk\'"}]}'
        ),
        **overrides,
    }


# ------------------------------------------------------------------ rows to inputs


def test_a_finding_row_becomes_an_alert_row() -> None:
    row = alert_row(
        finding(core_rule_ids="R1,R6", readiness_rule_ids="R8", unseen=True, fire_pattern="stuck")
    )
    assert row.schema == "v1" and row.key_field == "checkout-api:cart:node-1"
    assert row.core_rule_ids == ("R1", "R6") and row.readiness_rule_ids == ("R8",)
    assert row.unseen is True and row.fire_pattern == "stuck"
    assert alert_row(finding()).core_rule_ids == () and alert_row(finding()).unseen is None
    assert alert_row(finding(unseen=0)).unseen is False


def test_distinct_alerts_per_day_is_the_sum_of_daily_distinct_over_seven() -> None:
    days = [daily(18, distinct_alerts=5), daily(19, distinct_alerts=9), daily(20)]
    totals = schema_totals("v1", days, (alert_row(finding()),))
    assert totals.distinct_per_day == pytest.approx(17 / 7)
    assert totals.events == 30 and totals.rule_flagged_events == 6 and totals.suppressed == 3
    assert totals.distinct_alerts == 1


def test_schema_totals_count_states_readiness_and_keep_unseen_null_without_a_panel() -> None:
    alerts = (
        alert_row(finding(quality_state="rule_flagged", core_rule_ids="R1")),
        alert_row(finding(key_field="b", quality_state="rule_flagged", core_rule_ids="R4")),
        alert_row(finding(key_field="c", alert_schema="v2", readiness_rule_ids="R9")),
    )
    v1 = schema_totals("v1", [daily(20)], alerts)
    assert v1.states.get("rule_flagged") == 2 and v1.rule_flagged_alerts == 2
    assert v1.unseen is None and v1.unseen_alerts is None and v1.unseen_unmeasured is None
    v2 = schema_totals("v2", [], alerts)
    assert v2.events == 0 and v2.distinct_per_day == 0 and v2.readiness_gaps == 1


def test_unseen_is_summed_once_measured() -> None:
    days = [daily(19, unseen=2, unseen_unmeasured=1), daily(20, unseen=0, unseen_unmeasured=0)]
    alerts = (alert_row(finding(unseen=True)), alert_row(finding(key_field="b", unseen=False)))
    totals = schema_totals("v1", days, alerts)
    assert totals.unseen == 2 and totals.unseen_alerts == 1 and totals.unseen_unmeasured == 1


def test_rule_totals_sum_events_and_count_alerts_carrying_the_rule() -> None:
    counts = [
        {"alert_schema": "v1", "rule_id": "R10", "match_count": 1, "distinct_count": 1},
        {"alert_schema": "v1", "rule_id": "R1", "match_count": 4, "distinct_count": 1},
        {"alert_schema": "v1", "rule_id": "R1", "match_count": 3, "distinct_count": 2},
        {"alert_schema": "v2", "rule_id": "R8", "match_count": 2, "distinct_count": 1},
    ]
    alerts = (
        alert_row(finding(core_rule_ids="R1")),
        alert_row(finding(key_field="b", core_rule_ids="R1")),
        alert_row(finding(key_field="c", alert_schema="v2", readiness_rule_ids="R8")),
    )
    totals = rule_totals(counts, alerts)
    assert [(t.schema, t.rule_id) for t in totals] == [("v1", "R1"), ("v1", "R10"), ("v2", "R8")]
    r1 = totals[0]
    assert r1.events == 7 and r1.alerts == 2 and r1.distinct_per_day == pytest.approx(3 / 7)
    assert totals[1].alerts == 0


def test_basis_changes_between_consecutive_published_weeks() -> None:
    weeks = [
        {"ruleset_version": "1.0.0", "registry_version": "a"},
        {"ruleset_version": "1.0.0", "registry_version": "a"},
        {"ruleset_version": "1.1.0", "registry_version": "a"},
        {"ruleset_version": "1.1.0", "registry_version": "b"},
        {"ruleset_version": "1.1.0", "registry_version": "b"},
    ]
    assert basis_changes(weeks) == [False, False, True, True, False]
    assert basis_changes([]) == []


def test_the_snapshot_supplies_operators_panels_and_the_effort_override() -> None:
    entry = read_snapshot(run()["registry_entry_snapshot"])
    assert entry.v1_operators == ("checkout", "CHECKOUT") and entry.v2_operator == "checkout-v2"
    assert [p.panel_id for p in entry.panels] == ["main"] and entry.v1_rule_effort_days == 2.0
    empty = read_snapshot('{"team_id":"x"}')
    assert empty.panels == () and empty.v1_rule_effort_days is None
    assert read_snapshot("not json").panels == ()
    assert read_snapshot('{"planning":{"v1_rule_effort_days":"lots"}}').v1_rule_effort_days is None


def test_inputs_are_admin_inputs_with_both_schemas_and_no_history_when_unpublished() -> None:
    inputs = summary_inputs(
        run(),
        [daily(20)],
        [],
        (alert_row(finding()),),
        published=False,
        history=(),
    )
    assert inputs.surface == "admin" and inputs.team_id == "checkout-api"
    assert set(inputs.schemas) == {"v1", "v2"} and inputs.published is False
    assert inputs.history == () and inputs.v1_rule_effort_days == 2.0
    assert inputs.phase == "phase_1" and inputs.phase2_readiness_pct == 50.0


# ------------------------------------------------------------------ admin-only widgets


def test_panel_sql_is_escaped_and_its_suppression_clauses_marked() -> None:
    html, marked = highlight_panel_sql(
        "SELECT * FROM alerts WHERE a < 3 AND node_name != '<b>junk</b>' "
        "AND severity != 'warning' AND message NOT LIKE '%test%' "
        "AND (component NOT IN ('x', 'y') OR status = 'firing')"
    )
    assert "<b>" not in html and "&lt;b&gt;junk&lt;/b&gt;" in html and "a &lt; 3" in html
    assert marked
    marks = re.findall(r"<mark[^>]*>(.*?)</mark>", html)
    assert marks == [
        "node_name != &#x27;&lt;b&gt;junk&lt;/b&gt;&#x27;",
        "message NOT LIKE &#x27;%test%&#x27;",
        "component NOT IN (&#x27;x&#x27;, &#x27;y&#x27;)",
    ], "a classification field is scoping, never marked"
    assert html.count('class="sup unmeasured"') == 1, "the OR-nested clause is unmeasured"


def test_unparseable_panel_sql_is_shown_escaped_and_unmarked() -> None:
    html, marked = highlight_panel_sql("SELECT * WHERE node_name != 'x' AND ( <script>")
    assert not marked and "<mark" not in html and "&lt;script&gt;" in html


def test_day_by_day_plots_complete_utc_days_only() -> None:
    days = [
        daily(17, covered_hours=6, distinct_alerts=99),
        daily(18, distinct_alerts=4),
        daily(19, distinct_alerts=7),
        daily(24, covered_hours=18, distinct_alerts=98),
        daily(19, alert_schema="v2", distinct_alerts=2),
    ]
    html = day_by_day(days, "/teams/t/summary?run_id=x")
    assert html.count("<svg") == 2, "one chart per schema"
    assert ">7<" in html and ">99<" not in html and ">98<" not in html
    assert "<script" not in html and " style=" not in html


def test_the_worklist_filter_becomes_parameterised_sql() -> None:
    where, params = worklist_where("r" * 64, WorklistFilter(state="rule_flagged", rule="R1"))
    assert ":rule" in where and params["rule"] == "R1" and params["state"] == "rule_flagged"
    assert "schema" not in params
    where, params = worklist_where("r" * 64, WorklistFilter(schema="v2"))
    assert params == {"run_id": "r" * 64, "schema": "v2"}


# ------------------------------------------------------------------ the page


def _page(**changes: Any) -> str:
    inputs = summary_inputs(
        run(),
        [daily(20)],
        [],
        (alert_row(finding(message="<script>alert(1)</script>")),),
        published=False,
        history=(),
    )
    summary = AdminSummary(
        run=run(),
        inputs=inputs,
        entry=read_snapshot(run()["registry_entry_snapshot"]),
        daily=[daily(20)],
        stored_panels=[],
        runs=[run(), run(run_id="2" * 64, window_end=END - timedelta(days=7))],
        publication=changes.pop("publication", Publication("never")),
        schedule=changes.pop("schedule", None),
        worklist=inputs.alerts,
        total=1,
        filters=WorklistFilter(),
    )
    return summary_page("alice", summary, shared="")


def test_the_page_escapes_alert_text_and_carries_no_script_or_inline_style() -> None:
    html = _page()
    assert "<script" not in html and " style=" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Signed in as alice" in html


def test_the_page_header_carries_the_run_strip_and_the_run_picker() -> None:
    html = _page()
    for value in ("1" * 64, "1.1.0", "1.2.0", "2026-08-30.1", "fake-model-1", "checkout-v2"):
        assert value in html
    assert '<form class="picker" method="get"' in html
    assert f'<option value="{"1" * 64}" selected>' in html and f'value="{"2" * 64}"' in html


@pytest.mark.parametrize(
    ("publication", "expected"),
    [
        (Publication("never"), "never published"),
        (Publication("published", by="bob", at=END, note="All good"), "published"),
        (Publication("withdrawn", by="bob", at=END, note="Wrong week"), "withdrawn"),
    ],
)
def test_the_page_states_the_publication(publication: Publication, expected: str) -> None:
    html = _page(publication=publication)
    assert expected in html
    if publication.note:
        assert publication.note in html and "bob" in html


def test_the_page_states_the_last_schedule_outcome() -> None:
    assert "never scheduled" in _page()
    html = _page(schedule={"outcome": "held", "invoked_at": END, "detail": "3 unassessed"})
    assert "held" in html and "3 unassessed" in html
