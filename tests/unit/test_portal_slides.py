"""The two presentation slides at the bottom of the Summary, rendered from hand-built summaries.

The slides are screenshots waiting to happen: two fixed 16:9 frames that must hold every
portal rule the rest of the Summary holds - no script, no inline style, escaped alert text,
none of the words a reader must never see, v1 and v2 never added together - and must keep
long or numerous things inside the frame by capping and cutting them.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date

import pytest
from src.insights import KeyFinding, RuleTotal, TeamSummary
from src.portal.assets import STYLESHEET
from src.portal.explain import EN_DASH
from src.portal.slides import MESSAGE_LIMIT, render_slides
from src.portal.summary_view import render_summary_sections

from tests.unit.test_portal_summary_view import (
    ALERTS,
    FORBIDDEN,
    alert,
    build_summary,
    estimate,
    link,
    schema_totals,
)


def slides(summary: TeamSummary | None = None) -> str:
    return render_slides(summary or build_summary())


def frame(html: str, number: int) -> str:
    """The markup of slide ``number`` (1 or 2)."""
    frames = re.findall(r'<section class="slide .*?</section>', html, flags=re.S)
    assert len(frames) == 2
    return str(frames[number - 1])


def block(html: str, title: str) -> str:
    """One labelled block of a slide, up to the next label or the footer."""
    start = html.index(f'<h5 class="sl-label">{title}</h5>')
    rest = html[start + 1 :]
    ends = [i for i in (rest.find('<h5 class="sl-label">'), rest.find("<footer")) if i >= 0]
    return html[start : start + 1 + min(ends)]


def with_rules(summary: TeamSummary, rules: tuple[RuleTotal, ...]) -> TeamSummary:
    return dataclasses.replace(summary, inputs=dataclasses.replace(summary.inputs, rules=rules))


# ------------------------------------------------------------------ the section


def test_there_are_two_titled_slides_under_a_presentation_heading() -> None:
    html = slides()
    assert ">Presentation</h3>" in html
    assert "Two 16:9 slides for this week. Screenshot each frame and paste it as a slide." in html
    assert html.count('<section class="slide ') == 2
    assert "Where Data Pipeline / ETL stands" in frame(html, 1)
    assert "What to fix" in frame(html, 2)


def test_the_slides_close_the_summary() -> None:
    html = render_summary_sections(build_summary(), rule_link=link)
    assert html.index("Migration progress") < html.index(">Presentation</h3>")
    assert html.endswith("</section></div></section></div>"), "the last widget in the summary"


def test_each_frame_is_a_fixed_16_by_9_box_that_clips() -> None:
    rule = re.search(r"\.slide\{(.*?)\}", STYLESHEET, flags=re.S)
    assert rule is not None
    css = rule.group(1)
    for declaration in ("width:1280px", "height:720px", "aspect-ratio:16 / 9", "overflow:hidden"):
        assert declaration in css, declaration
    assert "overflow-x:auto" in STYLESHEET[STYLESHEET.index(".sl-scroll{") :]


def test_the_slides_keep_a_light_palette_of_their_own() -> None:
    """Projected slides stay light in dark mode: colours are set on .slide, not read from :root."""
    css = re.search(r"\.slide\{(.*?)\}", STYLESHEET, flags=re.S)
    assert css is not None
    assert "--sl-bg:#FFFFFF" in css.group(1) and "color-scheme:light" in css.group(1)
    block_css = STYLESHEET[STYLESHEET.index("Presentation slides") :]
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", block_css))
    page_tokens = {name for name in used if not name.startswith("--sl-")}
    assert page_tokens <= {"--sans", "--muted", "--line-strong"}, page_tokens


def test_both_slides_carry_the_footer_with_no_internals() -> None:
    html = slides()
    for number in (1, 2):
        assert "Data Pipeline / ETL · week ending 28 Sep 2026 · Alerts BI" in frame(html, number)


@pytest.mark.parametrize("surface", ["portal", "admin"])
def test_nothing_executes_nothing_is_styled_inline_and_nothing_links(surface: str) -> None:
    html = slides(build_summary(surface=surface))
    assert "<script" not in html.lower()
    assert " style=" not in html
    assert "href=" not in html, "a screenshot has nothing to click"


def test_the_copy_never_names_internals_or_daily_rates() -> None:
    summaries = [
        build_summary(),
        build_summary(alerts=(), findings=()),
        build_summary(est=estimate(projected_week_end=None, no_estimate_reason="Too few.")),
    ]
    for summary in summaries:
        html = slides(summary)
        for word in FORBIDDEN:
            assert word not in html, word
    css = STYLESHEET[STYLESHEET.index("Presentation slides") :]
    for word in FORBIDDEN:
        assert word not in css, word


# ------------------------------------------------------------------ slide 1


def test_the_subtitle_names_the_week_the_phase_and_readiness() -> None:
    one = frame(slides(), 1)
    assert f"Week of 21 Sep {EN_DASH} 28 Sep 2026 (UTC)" in one
    assert "Phase 1 · New rules" in one
    assert "0% phase-2 ready" in one

    unknown = build_summary()
    unknown = dataclasses.replace(
        unknown, inputs=dataclasses.replace(unknown.inputs, phase2_readiness_pct=None)
    )
    assert "phase-2 ready" not in frame(slides(unknown), 1)


def test_each_schema_has_its_own_figures_and_they_are_never_summed() -> None:
    one = frame(slides(), 1)
    v1 = one[one.index('class="sl-schema v1"') : one.index('class="sl-schema v2"')]
    v2 = one[one.index('class="sl-schema v2"') :]
    assert "<b>3</b><span>alerts</span>" in v1 and "1,024 events" in v1
    assert "2 rule-flagged alerts (96% of events)" in v1
    assert "<b>1</b><span>alert</span>" in v2 and "6 events" in v2
    assert "0 rule-flagged alerts (0% of events)" in v2
    for total in ("1,030", ">4<", "1030"):
        assert total not in one, f"v1 + v2 = {total}"


def test_the_examined_split_has_three_items_and_more_only_when_present() -> None:
    one = frame(slides(), 1)
    v1 = one[one.index('class="sl-schema v1"') : one.index('class="sl-schema v2"')]
    legend = v1[v1.index('<ul class="sl-legend">') :]
    for label in ("rule-flagged", "model-flagged (advisory)", "assessed good"):
        assert label in legend
    assert "not reviewed" not in legend and "needs a decision" not in legend
    assert '<svg class="sl-bar"' in v1

    states = {"rule_flagged": 2, "llm_flagged": 1, "unassessed": 4}
    summary = build_summary(
        schemas={"v1": schema_totals("v1", states=states), "v2": schema_totals("v2")}
    )
    assert "not reviewed <b>4</b>" in frame(slides(summary), 1)


def test_a_schema_with_no_alerts_says_so() -> None:
    quiet = schema_totals(
        "v2", events=0, distinct_alerts=0, states={}, readiness_gaps=0, rule_flagged_events=0
    )
    summary = build_summary(schemas={"v1": schema_totals("v1"), "v2": quiet})
    one = frame(slides(summary), 1)
    assert "No v2 alerts this week" in one
    assert "No v1 alerts this week" not in one


def test_only_the_first_three_key_findings_are_shown() -> None:
    findings = tuple(
        KeyFinding("largest", f"Finding {i}", f"Body {i}.", None, None) for i in range(5)
    )
    one = frame(slides(build_summary(findings=findings)), 1)
    findings_html = block(one, "Key findings")
    assert "<b>Finding 2</b> <span>Body 2.</span>" in findings_html
    assert "Finding 3" not in one and "Finding 4" not in one


def test_short_lists_are_padded_and_empty_ones_say_none() -> None:
    one = frame(slides(), 1)
    assert block(one, "Key findings").count('<li class="sl-pad">—</li>') == 1
    empty = frame(slides(build_summary(alerts=(), findings=())), 1)
    assert "None this week" in block(empty, "Key findings")
    assert "None this week" in block(empty, "Biggest single source")


def test_the_biggest_single_source_is_set_against_its_own_schema() -> None:
    biggest = block(frame(slides(), 1), "Biggest single source")
    assert "1 alert produced <b>864</b> of 1,024 v1 events (84%)" in biggest
    assert "etl-loader" in biggest
    assert "Ingest lag above 15 minutes on node-1" in biggest


def test_a_long_message_is_cut_with_an_ellipsis_and_escaped() -> None:
    message = "<b>Payment</b> gateway latency " + "very " * 40 + "high"
    loud = alert(key_field="loud", message=message, row_count=5000)
    summary = build_summary(alerts=(*ALERTS, loud))
    biggest = block(frame(slides(summary), 1), "Biggest single source")
    shown = re.search(r'<p class="sl-msg">“(.*?)”</p>', biggest)
    assert shown is not None
    assert "&lt;b&gt;Payment&lt;/b&gt;" in shown.group(1) and "<b>Payment" not in biggest
    assert shown.group(1).endswith("…")
    plain = shown.group(1).replace("&lt;", "<").replace("&gt;", ">")
    assert len(plain) == MESSAGE_LIMIT
    assert "high" not in plain


# ------------------------------------------------------------------ slide 2


def test_top_rules_are_core_rules_by_events_capped_at_five_and_kept_per_schema() -> None:
    rules = (
        RuleTotal("v1", "R1", 100, 1),
        RuleTotal("v1", "R2", 900, 3),
        RuleTotal("v1", "R3", 50, 1),
        RuleTotal("v1", "R4", 700, 2),
        RuleTotal("v1", "R6", 800, 2),
        RuleTotal("v2", "R6", 40, 1),
        RuleTotal("v1", "R7", 10, 1),
        RuleTotal("v2", "R8", 5000, 9),
        RuleTotal("v2", "R9", 4000, 9),
    )
    two = frame(slides(with_rules(build_summary(), rules)), 2)
    top = block(two, "Top rules")
    shown = re.findall(r'<span class="sl-rid">(R\d+)</span><span class="sl-chip (v\d)">', top)
    assert shown == [("R2", "v1"), ("R6", "v1"), ("R4", "v1"), ("R1", "v1"), ("R3", "v1")]
    assert "R8" not in top and "R9" not in top, "readiness gaps are not quality rules"
    assert "3 alerts · 900 events" in top
    assert "Rewrite the message" in top or "Delete this alert" in top
    assert "840" not in top, "R6 in v1 and v2 is never summed"


def test_r6_is_listed_per_schema_with_its_next_step() -> None:
    rules = (RuleTotal("v1", "R6", 800, 2), RuleTotal("v2", "R6", 40, 1))
    top = block(frame(slides(with_rules(build_summary(), rules)), 2), "Top rules")
    assert top.count('<span class="sl-rid">R6</span>') == 2
    assert "2 alerts · 800 events" in top and "1 alert · 40 events" in top
    assert top.count('<li class="sl-pad">—</li>') == 3


def test_noisiest_applications_keep_rule_and_model_figures_apart() -> None:
    apps = block(frame(slides(), 2), "Noisiest applications")
    loader = apps[apps.index("etl-loader") :]
    assert "<b>2</b> rule-flagged · <b>0</b> model (advisory)" in loader
    sync = apps[apps.index("warehouse-sync") :]
    assert "<b>0</b> rule-flagged · <b>1</b> model (advisory)" in sync
    assert apps.count("<li>") == 3


def test_firing_patterns_are_counted_per_schema_from_the_stored_pattern() -> None:
    extra = (
        alert(key_field="s1", fire_pattern="spamming"),
        alert(key_field="s2", fire_pattern="spamming"),
        alert(schema="v2", key_field="f1", fire_pattern="flapping"),
    )
    fire = block(frame(slides(build_summary(alerts=(*ALERTS, *extra))), 2), "Firing patterns")
    found = re.findall(r'<tr><th scope="row">(\w+)</th><td>(\d+)</td><td>(\d+)</td></tr>', fire)
    rows = {pattern: (v1, v2) for pattern, v1, v2 in found}
    assert rows == {"stuck": ("1", "0"), "spamming": ("2", "0"), "flapping": ("0", "1")}


def test_out_of_view_never_turns_a_missing_dashboard_into_zero() -> None:
    view = block(frame(slides(), 2), "Out of view")
    v1 = view[view.index('"sl-chip v1"') : view.index('"sl-chip v2"')]
    v2 = view[view.index('"sl-chip v2"') :]
    assert "120 events hidden by your own panels" in v1 and "1 alert on no dashboard" in v1
    assert "no dashboard supplied" in v2 and "0 events" not in v2

    panel_without_unseen = {
        "v1": schema_totals("v1", unseen=None, unseen_alerts=None),
        "v2": schema_totals("v2"),
    }
    view = block(frame(slides(build_summary(schemas=panel_without_unseen)), 2), "Out of view")
    v1 = view[view.index('"sl-chip v1"') : view.index('"sl-chip v2"')]
    assert "120 events hidden by your own panels" in v1 and "no dashboard supplied" in v1


def test_the_estimate_with_a_projected_week() -> None:
    time = block(frame(slides(), 2), "Time to finish phase 1")
    assert "week of 12 Oct 2026" in time
    assert "2 v1 alert rules left" in time
    assert "≈ 1 working days (0.2 weeks) at 0.5 days per rule, configured" in time
    assert "A projection. v1 falling may be cleanup rather than migration." in time


def test_the_estimate_without_a_date_states_its_reason() -> None:
    reason = "Needs at least 2 earlier published weeks back to back; found 0."
    est = estimate(
        projected_week_end=None,
        no_estimate_reason=reason,
        rules_left=1,
        effort_days=2.5,
        effort_weeks=0.5,
        effort_days_per_rule=2.5,
    )
    time = block(frame(slides(build_summary(est=est)), 2), "Time to finish phase 1")
    assert f"<b>No estimate:</b> {reason}" in time
    assert "week of" not in time
    assert "1 v1 alert rule left" in time
    assert "≈ 2.5 working days (0.5 weeks) at 2.5 days per rule, configured" in time


def test_the_projected_week_uses_the_estimate_date() -> None:
    est = estimate(projected_week_end=date(2027, 1, 4))
    assert "week of 4 Jan 2027" in frame(slides(build_summary(est=est)), 2)


def test_alert_and_team_text_is_escaped_everywhere() -> None:
    summary = build_summary(alerts=(alert(application="<i>app</i>", message="<script>x</script>"),))
    summary = dataclasses.replace(
        summary, inputs=dataclasses.replace(summary.inputs, display_name="<b>Team</b>")
    )
    html = slides(summary)
    assert "<i>" not in html and "<script" not in html and "<b>Team" not in html
    assert "&lt;i&gt;app&lt;/i&gt;" in html and "&lt;b&gt;Team&lt;/b&gt;" in html


def test_the_output_is_deterministic() -> None:
    assert slides() == slides()
