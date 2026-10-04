"""The team week in tabs (product owner, 2026-10-04), rendered from a hand-built summary.

Every tab keeps the portal's rules - no script, no inline style, escaped alert text, none of
the words a reader must never see, v1 and v2 never added together - and the design review's
own: no rule id in anything a reader reads, short copy, a week menu of plain links.
"""

from __future__ import annotations

import dataclasses
import html
import re
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from src.insights import TeamSummary
from src.portal import tabs
from src.portal.queries import (
    AlertDetail,
    AlertRow,
    Decision,
    Review,
    SchemaTotals,
    WorklistPage,
)
from src.rules.catalogs import R6_FLAP_CYCLES, R6_STUCK_OPEN

from tests.unit.test_portal_summary_view import ALERTS as SUMMARY_ALERTS
from tests.unit.test_portal_summary_view import FORBIDDEN, build_summary
from tests.unit.test_portal_summary_view import alert as summary_alert

END = datetime(2026, 9, 28)
HOSTILE = '<script>alert("x")</script>'
RULE_ID = re.compile(r"\bR(10|[1-9])\b")


def review(end: datetime = END, run_id: str = "r3", **overrides: object) -> Review:
    values: dict[str, object] = {
        "team_id": "team<x>",
        "team_name": f"Team {HOSTILE}",
        "run_id": run_id,
        "window_start": end - timedelta(hours=168),
        "window_end": end,
        "published_at": end + timedelta(hours=16),
        "review_note": None,
        "phase": "phase_1",
        "readiness_pct": 0.0,
        "totals": {
            "v1": SchemaTotals(events=1024, distinct_alerts=3, needs_attention=3),
            "v2": SchemaTotals(events=6, distinct_alerts=1, needs_attention=1),
        },
    }
    values.update(overrides)
    return Review(**values)  # type: ignore[arg-type]


REVIEWS = [review(END - timedelta(hours=168), "r2"), review()]
SELECTED = REVIEWS[-1]


def summary() -> TeamSummary:
    hostile = summary_alert(message=HOSTILE, application=f"app {HOSTILE}")
    return build_summary(alerts=(hostile, *SUMMARY_ALERTS[1:]))


def work_alert(**overrides: object) -> AlertRow:
    values: dict[str, object] = {
        "alert_schema": "v2",
        "application": f"app {HOSTILE}",
        "key_field": f"key {HOSTILE}",
        "message": HOSTILE,
        "severity": "critical",
        "component": "sms-send",
        "node_name": None,
        "environment": "production",
        "provider": "grafana",
        "alert_rule_url": "javascript:alert(1)",
        "row_count": 2,
        "first_seen": END - timedelta(hours=12),
        "last_seen": END,
        "core_rule_ids": (),
        "readiness_rule_ids": ("R9",),
        "evidence": {
            "R9": {
                "rule_id": "R9",
                "matched_rows": 2,
                "sample_evidence": {
                    "severity": "critical",
                    "blocks_completion": True,
                    "reason": "missing",
                },
            }
        },
        "quality_state": "needs_review",
        "llm_principle_id": "P2",
        "llm_confidence": "medium",
        "llm_justification": f"because {HOSTILE}",
        "impact": HOSTILE,
        "runbook_url": 'javascript:alert("runbook")',
        "alert_status": "firing",
        "time_created": None,
        "representative_at": END,
        "attention_rank": 2,
    }
    values.update(overrides)
    return AlertRow(**values)  # type: ignore[arg-type]


RULE_ALERT = work_alert(
    alert_schema="v1",
    application="etl-loader",
    key_field="etl-loader:ingest:node-2",
    message="Something went wrong",
    severity="error",
    core_rule_ids=("R1", "R4"),
    readiness_rule_ids=(),
    evidence={
        "R1": {
            "rule_id": "R1",
            "matched_rows": 3,
            "sample_evidence": {"normalized": "something went wrong"},
        },
        "R4": {"rule_id": "R4", "matched_rows": 120, "sample_evidence": {"provider": "grafana"}},
    },
    quality_state="rule_flagged",
    llm_principle_id=None,
    llm_confidence=None,
    llm_justification=None,
    row_count=120,
    attention_rank=0,
)
DECISION = Decision("P2", "pending", f"asked {HOSTILE}", END, f"op {HOSTILE}")


def fix(**filters: str) -> str:
    alerts = [RULE_ALERT, work_alert()]
    options = {"show": "attention", "schema": "all", "state": "all", "rule": "", **filters}
    return tabs.fix_page(
        REVIEWS,
        SELECTED,
        summary(),
        WorklistPage(alerts, len(alerts), 1, 25),
        {(a.alert_schema, a.application, a.key_field): {"P2": DECISION} for a in alerts[1:]},
        {"attention": 2, "all": 4},
        **options,
    )


def alert_detail(alert: AlertRow) -> str:
    return tabs.alert_page(REVIEWS, SELECTED, AlertDetail(alert, {"P2": [DECISION]}))


PAGES: dict[str, Callable[[], str]] = {
    "overview": lambda: tabs.overview_page(REVIEWS, SELECTED, summary()),
    "fix": fix,
    "fix-rule": lambda: fix(rule="R6"),
    "volume": lambda: tabs.volume_page(REVIEWS, SELECTED, summary()),
    "dashboards": lambda: tabs.dashboards_page(REVIEWS, SELECTED, summary()),
    "migration": lambda: tabs.migration_page(REVIEWS, SELECTED, summary()),
    "history": lambda: tabs.history_page(REVIEWS, SELECTED),
    "slides": lambda: tabs.slides_page(REVIEWS, SELECTED, summary()),
    "alert-review": lambda: alert_detail(work_alert()),
    "alert-rule": lambda: alert_detail(RULE_ALERT),
}


def visible(page: str) -> str:
    """The text a reader reads: tags, and so link targets, removed."""
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


# ------------------------------------------------------------------ every tab


@pytest.mark.parametrize("name", PAGES)
def test_nothing_executes_and_nothing_is_styled_inline(name: str) -> None:
    page = PAGES[name]()
    assert "<script" not in page and "&lt;script&gt;" in page
    assert " style=" not in page, "inline styles would need unsafe-inline in the CSP"
    assert 'href="javascript:' not in page


@pytest.mark.parametrize("name", PAGES)
def test_no_rule_id_and_no_internal_word_is_shown(name: str) -> None:
    text = visible(PAGES[name]())
    assert not RULE_ID.search(text), RULE_ID.findall(text)
    for word in FORBIDDEN:
        assert word not in text, word
    assert "r3" not in text, "no run id"


@pytest.mark.parametrize("name", PAGES)
def test_every_tab_has_the_tab_bar_and_the_week_menu(name: str) -> None:
    page = PAGES[name]()
    nav = page[page.index('<nav class="tabs"') :]
    nav = nav[: nav.index("</nav>")]
    labels = re.findall(r">([A-Z][a-z]+(?: list)?)(?: <span|</a>)", nav)
    assert labels == [
        "Overview",
        "Fix list",
        "Volume",
        "Dashboards",
        "Migration",
        "History",
        "Slides",
    ]
    assert nav.count('aria-current="page"') == 1
    assert '<span class="cnt">4</span>' in nav, "the Fix list counts alerts that need attention"
    assert "<select" not in page and ">Open</button>" not in page, "the week menu is links"
    assert "published" not in visible(page), "no date or publication line under the name"


def test_the_week_menu_keeps_the_open_tab_and_marks_the_week() -> None:
    page = PAGES["migration"]()
    menu = page[page.index('<details class="menu">') :]
    menu = menu[: menu.index("</details>")]
    assert menu.count("/migration") == 2, "one link per published week, to the same tab"
    assert "/weeks/2026-09-21/migration" in menu and "/weeks/2026-09-28/migration" in menu
    assert menu.index("2026-09-28") < menu.index("2026-09-21"), "newest first"
    assert menu.count('aria-current="page"') == 1


# ------------------------------------------------------------------ overview


def test_the_overview_cards_name_the_schema_once() -> None:
    page = PAGES["overview"]()
    assert "<h2>Appchi</h2>" in page and "<h2>Appchi V2</h2>" in page
    assert '<span class="chip v2">v2</span> Appchi V2' not in page
    assert "Where they came from" not in page
    text = visible(page)
    assert "1,024" in text and "1,030" not in text, "v1 and v2 events are never added together"


def test_each_key_finding_links_to_the_tab_that_answers_it() -> None:
    page = PAGES["overview"]()
    assert "Biggest problem: firing pattern" in page
    assert "/weeks/2026-09-28/fix?rule=R6" in page
    assert "/weeks/2026-09-28/dashboards" in page


def test_the_review_note_leads_the_overview() -> None:
    noted = dataclasses.replace(SELECTED, review_note=f"Held {HOSTILE}")
    page = tabs.overview_page(REVIEWS, noted, summary())
    assert "Held &lt;script&gt;" in page
    assert page.index('class="note"') < page.index("Key findings")


# ------------------------------------------------------------------ fix list


def test_what_to_change_groups_problems_with_each_schema_apart() -> None:
    page = fix()
    table = page[page.index("What to change") : page.index('id="alerts"')]
    order = [table.index(cls) for cls in ('"grp g-fix"', '"grp g-adv"', '"grp g-ready"')]
    assert order == sorted(order)
    assert "Fix stuck, spamming or flapping alerts" in table
    assert "Stop hiding alerts in panels" in table and "Rewrite generic messages" in table
    assert "Read the advisory findings" in table
    # The fixture's only v2 runbook gap is on a critical alert, so it blocks phase 2.
    assert "Add a runbook (1 critical)" in table and "Add an impact" in table
    assert table.index("Add a runbook") < table.index("Add an impact"), "the blocker first"
    assert "/fix?rule=R6#alerts" in table
    assert "1,024" not in table and "1,030" not in table


def test_the_problem_filter_explains_the_problem_and_its_fix() -> None:
    page = fix(rule="R6")
    assert "What to change" not in page
    assert "← All changes" in page
    hours = int(R6_STUCK_OPEN.total_seconds() // 3600)
    assert f"It kept firing for {hours} hours or more without clearing." in page
    assert "<b>Fix:</b>" in page
    assert '<span class="chip rule">Firing pattern</span>' in page


def test_the_filters_keep_each_other_and_are_links() -> None:
    page = fix(state="rule_flagged", rule="R1")
    assert "schema=v1&amp;state=rule_flagged&amp;rule=R1#alerts" in page
    assert 'href="/teams/team%3Cx%3E/weeks/2026-09-28/fix?state=rule_flagged#alerts"' in page
    assert "<form" not in page


def test_a_work_list_row_names_its_problems_and_decisions() -> None:
    rows = fix()[fix().index('id="alerts"') :]
    assert '<span class="chip rule">Generic message</span>' in rows
    assert '<span class="chip rule">No rule link</span>' in rows
    assert '<span class="chip review">Needs decision</span>' in rows
    assert '<span class="chip ready">Critical, no runbook</span>' in rows
    assert '<span class="chip human pending"' in rows


# ------------------------------------------------------------------ the other tabs


def test_volume_lists_the_loudest_alerts_and_no_application_table() -> None:
    page = PAGES["volume"]()
    assert "Loudest alerts" in page and "By application" not in page
    assert "1 stuck" in page
    assert f"{int(R6_STUCK_OPEN.total_seconds() // 3600)} hours or more" in page
    assert f"{R6_FLAP_CYCLES} or more times in a day" in page


def test_volume_says_so_when_nothing_fires_in_a_pattern() -> None:
    calm = build_summary(
        alerts=tuple(dataclasses.replace(a, fire_pattern=None) for a in SUMMARY_ALERTS)
    )
    page = tabs.volume_page(REVIEWS, SELECTED, calm)
    assert "No stuck, spamming or flapping alerts" in page
    assert '<th scope="col">Pattern</th>' not in page


def test_dashboards_shows_hidden_alerts_and_not_measured_rather_than_zero() -> None:
    page = PAGES["dashboards"]()
    assert "Hidden by your panels" in page and "Something went wrong" in page
    assert "Remove the panel filter" in page or "remove the panel filter" in page
    unseen = page[page.index("On no dashboard") :]
    assert "40 events · 1 alert" in unseen
    assert "Not measured this week" in unseen, "v2 has no panel: never shown as zero"


def test_dashboards_claims_nothing_hidden_only_for_what_was_checked() -> None:
    quiet = build_summary(
        schemas={
            "v1": dataclasses.replace(summary().inputs.schemas["v1"], suppressed=0),
            "v2": summary().inputs.schemas["v2"],
        }
    )
    page = tabs.dashboards_page(REVIEWS, SELECTED, quiet)
    assert "None found in the filters we could check." in page


def test_migration_has_no_estimate() -> None:
    text = visible(PAGES["migration"]())
    for gone in ("When could", "working day", "pace", "projection", "Projection"):
        assert gone not in text, gone
    assert "Critical, no runbook" in text and "Pipeline backlog above 10000 rows" in text
    assert "Left to move" in text and "Ready for phase 2" in text


def test_history_plots_each_schema_apart_and_lists_the_weeks() -> None:
    page = PAGES["history"]()
    assert page.count("<polyline") == 4, "two schemas x two measures, two adjacent weeks"
    assert 'class="current"' in page and ">Viewing<" in page
    assert page.count(">Open</a>") == 1


def test_slides_keep_the_two_frames() -> None:
    page = PAGES["slides"]()
    assert page.count('<section class="slide ') == 2


# ------------------------------------------------------------------ the alert page


def test_the_alert_page_labels_the_sample_apart_from_the_latest_event() -> None:
    page = PAGES["alert-rule"]()
    assert "Matched event · stored sample" in page and "Latest event" in page
    assert "3 of 120 events matched." in page
    assert "Skipped: the problems above already say what to fix." in page
    assert page.count('aria-current="page"') == 2, "the Fix list tab and the week"


def test_the_alert_page_shows_the_decision_to_make_and_marks_it_advisory() -> None:
    page = PAGES["alert-review"]()
    assert "<b>Decide:</b>" in page and "advisory" in page
    assert "because &lt;script&gt;" in page, "the model's reasoning, escaped"
    assert "Get v2 ready" in page and "Critical, no runbook" in page
    assert "starts without one" in page, "a decision never carries to an enriched key"
    assert "not a usable http(s) link" in page
