"""The shared Summary widgets (team summary spec section 4), rendered from a hand-built summary.

The renderer is pure: it takes a :class:`TeamSummary` and returns markup. These tests hold it
to the portal's rules - no script, no inline style, escaped alert text, none of the words a
reader must never see - and to the surface split: per-day rates and unmeasured counts appear
on the operator surface only.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date, datetime, timedelta

import pytest
from src.insights import (
    AlertRow,
    AppRow,
    Estimate,
    FireRow,
    KeyFinding,
    RuleTotal,
    SchemaTotals,
    SummaryInputs,
    TeamSummary,
    WeekRules,
)
from src.portal.charts import TIMES
from src.portal.summary_view import format_projected_week, render_summary_sections

END = datetime(2026, 9, 28)
START = END - timedelta(hours=168)

#: Substrings a reader never sees in the portal's own copy (design section 7.10).
FORBIDDEN = ("per day", "run_id", "registry", "ruleset", "prompt", "model version")


def alert(**overrides: object) -> AlertRow:
    values: dict[str, object] = {
        "schema": "v1",
        "application": "etl-loader",
        "key_field": "etl-loader:ingest:node-1",
        "message": "Ingest lag above 15 minutes on node-1",
        "severity": "error",
        "provider": "grafana",
        "alert_rule_url": "https://grafana.internal/alerting/etl-lag",
        "component": "ingest",
        "node_name": "node-1",
        "row_count": 864,
        "first_seen": END - timedelta(hours=72),
        "last_seen": END - timedelta(minutes=5),
        "quality_state": "rule_flagged",
        "core_rule_ids": ("R6",),
        "readiness_rule_ids": (),
        "llm_principle_id": None,
        "llm_confidence": None,
        "clear_count": 0,
        "max_clear_cycles_24h": 0,
        "fire_pattern": "stuck",
        "unseen": False,
    }
    values.update(overrides)
    return AlertRow(**values)  # type: ignore[arg-type]


ALERTS = (
    alert(),
    alert(
        application="etl-loader",
        key_field="etl-loader:ingest:node-2",
        message="Something went wrong",
        row_count=120,
        core_rule_ids=("R1", "R5"),
        fire_pattern=None,
    ),
    alert(
        application="warehouse-sync",
        key_field="warehouse-sync:job:node-3",
        message="Warehouse sync job failed with exit code 1",
        row_count=40,
        quality_state="llm_flagged",
        core_rule_ids=(),
        llm_principle_id="P3",
        llm_confidence="high",
        fire_pattern=None,
        unseen=True,
    ),
    alert(
        schema="v2",
        application="etl-api",
        key_field="9f0c1d2e3a4b5c6d",
        message="Pipeline backlog above 10000 rows",
        severity="critical",
        provider="api",
        alert_rule_url=None,
        row_count=6,
        first_seen=END - timedelta(hours=60),
        quality_state="assessed_good",
        core_rule_ids=(),
        readiness_rule_ids=("R8", "R9"),
        llm_principle_id="NONE",
        llm_confidence="high",
        fire_pattern=None,
        unseen=None,
    ),
)


def schema_totals(schema: str, **overrides: object) -> SchemaTotals:
    if schema == "v1":
        values: dict[str, object] = {
            "schema": "v1",
            "events": 1024,
            "distinct_alerts": 3,
            "distinct_per_day": None,
            "rule_flagged_events": 984,
            "rule_flagged_alerts": 2,
            "suppressed": 120,
            "unseen": 40,
            "unseen_alerts": 1,
            "states": {"rule_flagged": 2, "llm_flagged": 1},
            "readiness_gaps": 0,
        }
    else:
        values = {
            "schema": "v2",
            "events": 6,
            "distinct_alerts": 1,
            "distinct_per_day": None,
            "rule_flagged_events": 0,
            "rule_flagged_alerts": 0,
            "suppressed": 0,
            "unseen": None,
            "unseen_alerts": None,
            "states": {"assessed_good": 1},
            "readiness_gaps": 1,
        }
    values.update(overrides)
    return SchemaTotals(**values)  # type: ignore[arg-type]


RULES = (
    RuleTotal("v1", "R1", 120, 1),
    RuleTotal("v1", "R5", 120, 1),
    RuleTotal("v1", "R6", 864, 1),
    RuleTotal("v2", "R8", 6, 1),
    RuleTotal("v2", "R9", 6, 1),
)


def estimate(**overrides: object) -> Estimate:
    values: dict[str, object] = {
        "rules_left": 2,
        "lookback_weeks": 3,
        "retired": 3,
        "pace_per_week": 1.0,
        "projected_week_end": date(2026, 10, 12),
        "no_estimate_reason": None,
        "effort_days_per_rule": 0.5,
        "effort_is_override": False,
        "effort_days": 1.0,
        "effort_weeks": 0.2,
    }
    values.update(overrides)
    return Estimate(**values)  # type: ignore[arg-type]


def build_summary(
    *,
    surface: str = "portal",
    alerts: tuple[AlertRow, ...] = ALERTS,
    schemas: dict[str, SchemaTotals] | None = None,
    est: Estimate | None = None,
    findings: tuple[KeyFinding, ...] | None = None,
) -> TeamSummary:
    inputs = SummaryInputs(
        surface=surface,  # type: ignore[arg-type]
        team_id="data-pipeline",
        display_name="Data Pipeline / ETL",
        window_start=START,
        window_end=END,
        phase="phase_1",
        phase2_readiness_pct=0.0,
        schemas=schemas or {"v1": schema_totals("v1"), "v2": schema_totals("v2")},
        rules=RULES,
        alerts=alerts,
        published=True,
        history=(WeekRules(END, frozenset({"url:x"}), False),),
        v1_rule_effort_days=None,
    )
    fire = tuple(
        FireRow(
            alert=a,
            span_hours=72.0 + (5 / 60 if a.schema == "v1" else 12),
            ratio=None if a.provider == "api" else a.row_count / 865,
            events_per_24h=a.row_count / 3,
            pattern=a.fire_pattern,
        )
        for a in sorted(alerts, key=lambda a: -a.row_count)
    )
    return TeamSummary(
        inputs=inputs,
        key_findings=findings
        if findings is not None
        else (
            KeyFinding(
                "largest",
                "R6 is your largest finding",
                "864 v1 events from 1 alert.",
                None,
                "R6",
            ),
            KeyFinding(
                "unseen",
                "Some alerts reach none of your dashboards",
                "1 alerts (40 events) are outside every panel's narrowing.",
                "Widen a panel to include them, or confirm they are meant to stay out of view.",
                None,
            ),
        ),
        by_application=(
            AppRow("etl-loader", "v1", 2, 2, 0, 984, 984, 0, ("R1", "R5", "R6")),
            AppRow("warehouse-sync", "v1", 1, 0, 1, 40, 0, 40, ()),
            AppRow("etl-api", "v2", 1, 0, 0, 6, 0, 0, ()),
        ),
        fire=fire,
        biggest=max(alerts, key=lambda a: a.row_count) if alerts else None,
        estimate=est or estimate(),
    )


def link(rule_id: str | None) -> str:
    return f"/wl?rule={rule_id or 'any'}"


def render(summary: TeamSummary | None = None) -> str:
    return render_summary_sections(summary or build_summary(), rule_link=link)


# ------------------------------------------------------------------ the portal's rules


@pytest.mark.parametrize("surface", ["portal", "admin"])
def test_nothing_executes_and_nothing_is_styled_inline(surface: str) -> None:
    html = render(build_summary(surface=surface))
    assert "<script" not in html.lower()
    assert " style=" not in html
    assert 'href="javascript:' not in html


def test_the_portal_copy_never_names_internals_or_daily_rates() -> None:
    """The summary is built from clean alert text, so every hit here is our own copy."""
    html = render()
    for word in FORBIDDEN:
        assert word not in html, word


def test_the_portal_copy_stays_clean_in_every_branch() -> None:
    branches = [
        build_summary(est=estimate(projected_week_end=None, no_estimate_reason="Too few.")),
        build_summary(est=estimate(rules_left=0, effort_days=0.0, effort_weeks=0.0)),
        build_summary(est=estimate(effort_is_override=True, effort_days_per_rule=2.0)),
        build_summary(alerts=(), findings=()),
        build_summary(
            schemas={
                "v1": schema_totals("v1", unseen=None, unseen_alerts=None, suppressed=0),
                "v2": schema_totals("v2"),
            }
        ),
    ]
    for summary in branches:
        html = render(summary)
        for word in FORBIDDEN:
            assert word not in html, word


def test_alert_text_is_escaped() -> None:
    hostile = alert(message="<b>bold</b> & <script>x</script>", application="<i>app</i>")
    html = render(build_summary(alerts=(hostile,)))
    assert "<b>bold</b>" not in html and "<i>app</i>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "&lt;i&gt;app&lt;/i&gt;" in html


def test_an_alert_rule_url_becomes_a_link_only_when_it_is_http() -> None:
    risky = alert(alert_rule_url="javascript:alert(1)")
    html = render(build_summary(alerts=(risky,)))
    assert 'href="javascript:' not in html
    safe = render(build_summary(alerts=(alert(),)))
    assert 'href="https://grafana.internal/alerting/etl-lag"' in safe


# ------------------------------------------------------------------ the widgets


def test_every_widget_is_present_in_order() -> None:
    html = render()
    headings = [
        "distinct alerts this week",
        "Why alerts were flagged",
        "Key findings",
        "Noisy alerts by application",
        "How often alerts fire",
        "Biggest single source",
        "Flagged by rule",
        "Hidden by your own panels",
        "Not on any of your dashboards",
        "Migration progress",
    ]
    positions = [html.index(text) for text in headings]
    assert positions == sorted(positions)


def test_v1_and_v2_are_never_added_together() -> None:
    html = render()
    assert "1,024" in html and ">6<" in html
    assert "1,030" not in html, "v1 + v2 events"


def test_a_schema_with_no_panel_says_no_dashboard_was_supplied() -> None:
    html = render()
    unseen = html[html.index("Not on any of your dashboards") : html.index("Migration progress")]
    assert "No dashboard supplied" in unseen
    assert "40" in unseen, "the v1 count is still shown"


def test_no_dashboard_is_never_shown_as_zero() -> None:
    summary = build_summary(
        schemas={
            "v1": schema_totals("v1", unseen=None, unseen_alerts=None),
            "v2": schema_totals("v2"),
        }
    )
    html = render(summary)
    unseen = html[html.index("Not on any of your dashboards") : html.index("Migration progress")]
    assert unseen.count("No dashboard supplied") == 2


def test_the_fire_table_shows_the_ratio_bar_with_its_ticks_and_the_thresholds() -> None:
    html = render()
    fire = html[html.index("How often alerts fire") : html.index("Biggest single source")]
    assert f"1{TIMES}</text>" in fire and f"2{TIMES}</text>" in fire, "the ratio bar's ticks"
    assert '<svg class="ratio"' in fire
    assert "stuck" in fire
    for words in ("3 or more", f"2{TIMES}", f"0.9{TIMES}", "72 hours", "24 or more", "6 hours"):
        assert words in fire, words


def test_the_fire_table_shows_at_most_eight_alerts() -> None:
    many = tuple(
        alert(key_field=f"k{i}", message=f"Alert number {i}", row_count=100 - i) for i in range(12)
    )
    html = render(build_summary(alerts=many))
    fire = html[html.index("How often alerts fire") : html.index("Biggest single source")]
    assert "Alert number 7" in fire and "Alert number 8" not in fire


def test_rule_links_come_from_the_callback() -> None:
    calls: list[str | None] = []

    def recording(rule_id: str | None) -> str:
        calls.append(rule_id)
        return link(rule_id)

    html = render_summary_sections(build_summary(), rule_link=recording)
    assert {rule.rule_id for rule in RULES} <= set(calls)
    for href in re.findall(r'href="([^"]*)"', html):
        assert href.startswith(("/wl?rule=", "https://")), href


def test_a_key_finding_on_a_rule_offers_its_next_step_and_its_alerts() -> None:
    html = render()
    findings = html[html.index("Key findings") : html.index("Noisy alerts by application")]
    assert "R6 is your largest finding" in findings
    assert 'href="/wl?rule=R6"' in findings
    assert "Widen a panel" in findings


def test_flagged_by_rule_explains_each_rule() -> None:
    html = render()
    table = html[html.index("Flagged by rule") : html.index("Hidden by your own panels")]
    assert "Generic message" in table
    assert "Rewrite the message" in table, "the next step from explain"
    assert "etl-loader" in table, "top applications"


def test_hidden_lists_the_hidden_alerts_without_any_panel_text() -> None:
    html = render()
    hidden = html[html.index("Hidden by your own panels") : html.index("Not on any")]
    assert "120" in hidden and "Something went wrong" in hidden
    assert "SELECT" not in hidden and "WHERE" not in hidden


# ------------------------------------------------------------------ the estimate


def test_the_projected_week_is_formatted_as_a_week() -> None:
    assert format_projected_week(date(2026, 10, 12)) == "week of 12 Oct 2026"
    assert "week of 12 Oct 2026" in render()


def test_no_estimate_states_its_reason() -> None:
    reason = "Needs at least 2 earlier published weeks back to back; found 0."
    html = render(build_summary(est=estimate(projected_week_end=None, no_estimate_reason=reason)))
    assert "No estimate" in html and reason in html
    assert "week of" not in html


def test_effort_is_labelled_configured_not_measured_and_both_are_projections() -> None:
    html = render()
    progress = html[html.index("Migration progress") :]
    assert "configured, not measured" in progress
    assert "projection" in progress.lower()
    assert "cleanup rather than migration" in progress
    assert "monitoring" in progress
    assert "0.5" in progress


def test_a_team_override_is_named_as_such() -> None:
    html = render(build_summary(est=estimate(effort_is_override=True, effort_days_per_rule=2.0)))
    assert "set for this team" in html


def test_no_v1_rules_left_says_so_and_effort_is_zero() -> None:
    html = render(
        build_summary(
            est=estimate(
                rules_left=0,
                projected_week_end=None,
                no_estimate_reason="Too few.",
                effort_days=0.0,
                effort_weeks=0.0,
            )
        )
    )
    assert "no v1 alert rules left" in html
    assert "0 working days" in html


# ------------------------------------------------------------------ the two surfaces


def test_per_day_rates_and_unmeasured_counts_are_for_operators_only() -> None:
    schemas = {
        "v1": schema_totals(
            "v1", distinct_per_day=2.6, suppression_unmeasured=2, unseen_unmeasured=1
        ),
        "v2": schema_totals("v2", distinct_per_day=1.0),
    }
    admin = render(build_summary(surface="admin", schemas=schemas))
    assert "per day" in admin
    assert "2.6" in admin
    assert "unmeasured" in admin

    portal_schemas = {
        name: dataclasses.replace(totals, distinct_per_day=None) for name, totals in schemas.items()
    }
    portal = render(build_summary(surface="portal", schemas=portal_schemas))
    assert "per day" not in portal
    assert "unmeasured" not in portal
    assert "distinct alerts this week" in portal
