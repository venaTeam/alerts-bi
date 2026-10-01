"""The two presentation slides at the bottom of the Summary, rendered from hand-built summaries.

The slides are screenshots waiting to happen: two fixed 16:9 frames that must hold every
portal rule the rest of the Summary holds - no script, no inline style, escaped alert text,
none of the words a reader must never see, v1 and v2 never added together - and must keep
long or numerous things inside the frame by capping and cutting them.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date, timedelta

import pytest
from src.insights import DailyPoint, KeyFinding, RuleTotal, TeamSummary
from src.portal.assets import STYLESHEET
from src.portal.explain import EN_DASH
from src.portal.pages import h
from src.portal.slides import CHART_H, MESSAGE_LIMIT, PARTIAL_NOTE, percent, render_slides
from src.portal.summary_view import render_summary_sections

from tests.unit.test_portal_summary_view import (
    ALERTS,
    FORBIDDEN,
    START,
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


def week(
    schema: str, distinct: list[int], flagged: list[int], hours: list[float] | None = None
) -> tuple[DailyPoint, ...]:
    """One schema's UTC day buckets, starting on the fixture week's first day."""
    covered = hours or [24.0] * len(distinct)
    return tuple(
        DailyPoint(schema, (START + timedelta(days=i)).date(), covered[i], distinct[i], flagged[i])
        for i in range(len(distinct))
    )


V1_WEEK = week("v1", [3, 3, 2, 3, 3, 2, 3], [2, 2, 1, 2, 2, 1, 2])
V2_WEEK = week("v2", [0, 0, 1, 1, 1, 1, 1], [0, 0, 0, 0, 0, 0, 0])


def with_daily(summary: TeamSummary, daily: tuple[DailyPoint, ...]) -> TeamSummary:
    return dataclasses.replace(summary, inputs=dataclasses.replace(summary.inputs, daily=daily))


def charted(**kwargs: object) -> TeamSummary:
    return with_daily(build_summary(**kwargs), V1_WEEK + V2_WEEK)  # type: ignore[arg-type]


# ------------------------------------------------------------------ the section


def test_there_are_two_titled_slides_under_a_presentation_heading() -> None:
    html = slides()
    assert ">Presentation</h3>" in html
    assert "Two 16:9 slides for this week. Screenshot each frame and paste it as a slide." in html
    assert html.count('<section class="slide ') == 2
    assert "Where Data Pipeline / ETL stands" in frame(html, 1)
    assert "The week, day by day" in frame(html, 2)
    assert "What to fix" not in html


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
    html = slides(with_daily(build_summary(surface=surface), V1_WEEK + V2_WEEK))
    assert '<svg class="sl-chart"' in html
    assert "<script" not in html.lower()
    assert " style=" not in html
    assert "href=" not in html, "a screenshot has nothing to click"


def test_the_copy_never_names_internals_or_daily_rates() -> None:
    summaries = [
        build_summary(),
        build_summary(alerts=(), findings=()),
        build_summary(est=estimate(projected_week_end=None, no_estimate_reason="Too few.")),
        charted(),
        with_daily(build_summary(), week("v1", [1, 2], [0, 1], [6.0, 18.0])),
    ]
    for summary in summaries:
        html = slides(summary)
        for word in FORBIDDEN:
            assert word not in html, word
    css = STYLESHEET[STYLESHEET.index("Presentation slides") :]
    for word in FORBIDDEN:
        assert word not in css, word


def test_no_text_inside_a_frame_is_smaller_than_a_footnote() -> None:
    """Primary text is 20px and secondary 18px by default; nothing in a frame goes below 14px."""
    css = STYLESHEET[STYLESHEET.index(".slide{") :]
    sizes = [int(size) for size in re.findall(r"font-size:(\d+)px", css)]
    assert sizes and min(sizes) >= 14, sorted(sizes)
    for rule in (".sl-chart-legend{", ".sl-end{", ".sl-fire-line{", ".sl-nc{", ".sl-af{"):
        declarations = css[css.index(rule) : css.index("}", css.index(rule))]
        assert "font-size:18px" in declarations, rule


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
    one = frame(slides(unknown), 1)
    assert "<span>Phase-2 readiness: —</span>" in one
    assert "phase-2 ready" not in one


def readiness_of(pct: float | None, phase: str = "phase_1") -> str:
    summary = build_summary()
    summary = dataclasses.replace(
        summary,
        inputs=dataclasses.replace(summary.inputs, phase2_readiness_pct=pct, phase=phase),
    )
    sub = re.search(r'<p class="sl-sub">(.*?)</p>', frame(slides(summary), 1))
    assert sub is not None
    return str(sub.group(1))


def test_a_week_with_no_alerts_has_no_readiness_to_show() -> None:
    sub = readiness_of(0.0, phase="no_data")
    assert "Phase-2 readiness: —" in sub and "0%" not in sub


@pytest.mark.parametrize(
    ("pct", "shown"),
    [(12.5, "13%"), (2.5, "3%"), (0.4, "<1%"), (99.6, "99%"), (100.0, "100%"), (0.0, "0%")],
)
def test_readiness_uses_the_shared_percent_rules(pct: float, shown: str) -> None:
    assert f"<span>{h(shown)} phase-2 ready</span>" in readiness_of(pct)


@pytest.mark.parametrize(
    ("part", "whole", "shown"),
    [
        (1, 8, "13%"),  # 12.5 rounds half up, never to even
        (5, 200, "3%"),  # 2.5 -> 3, where round() gives 2
        (1, 1000, "<1%"),  # something is never "0%"
        (5000, 1234567, "<1%"),
        (996, 1000, "99%"),  # 99.6 is not done
        (999999, 1000000, "99%"),
        (7, 7, "100%"),  # only the whole is 100%
        (0, 7, "0%"),
        (3, 0, "0%"),
    ],
)
def test_percent_rounds_half_up_and_never_overstates(part: int, whole: int, shown: str) -> None:
    assert percent(part, whole) == shown


def test_shares_on_the_slide_use_the_shared_percent_rules() -> None:
    loud = alert(key_field="loud", row_count=5000)
    v1 = schema_totals("v1", events=1234567, rule_flagged_events=1)
    summary = build_summary(alerts=(*ALERTS, loud), schemas={"v1": v1, "v2": schema_totals("v2")})
    one = frame(slides(summary), 1)
    assert "2 rule-flagged alerts (&lt;1% of events)" in one
    assert "1 alert produced <b>5,000</b> of 1,234,567 v1 events (&lt;1%)" in one


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


def test_key_findings_wrap_to_two_lines_rather_than_one() -> None:
    css = STYLESHEET[STYLESHEET.index("Presentation slides") :]
    rule = css[css.index(".sl-lines li{") :]
    rule = rule[: rule.index("}")]
    for declaration in ("display:-webkit-box", "-webkit-line-clamp:2", "max-height:2.5em"):
        assert declaration in rule, declaration
    nowrap = css[: css.index("{white-space:nowrap;")]
    assert ".sl-lines li" not in nowrap[nowrap.rindex("}") :]


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
    assert "None this week" in block(empty, "Biggest single alert")


def test_the_biggest_single_alert_says_what_one_alert_is() -> None:
    biggest = block(frame(slides(), 1), "Biggest single alert")
    assert (
        '<p class="sl-def">Based on the alert identity: the application field plus the alert '
        "key.</p>"
    ) in biggest
    assert "1 alert produced <b>864</b> of 1,024 v1 events (84%)" in biggest
    assert "Application: <b>etl-loader</b>" in biggest
    assert "Message: “Ingest lag above 15 minutes on node-1”" in biggest
    assert "Biggest single source" not in frame(slides(), 1)


def test_a_long_message_is_cut_with_an_ellipsis_and_escaped() -> None:
    message = "<b>Payment</b> gateway latency " + "very " * 40 + "high"
    loud = alert(key_field="loud", message=message, row_count=5000)
    summary = build_summary(alerts=(*ALERTS, loud))
    biggest = block(frame(slides(summary), 1), "Biggest single alert")
    shown = re.search(r'<p class="sl-msg">Message: “(.*?)”</p>', biggest)
    assert shown is not None
    assert "&lt;b&gt;Payment&lt;/b&gt;" in shown.group(1) and "<b>Payment" not in biggest
    assert shown.group(1).endswith("…")
    plain = shown.group(1).replace("&lt;", "<").replace("&gt;", ">")
    assert len(plain) == MESSAGE_LIMIT
    assert "high" not in plain


# ------------------------------------------------------------------ slide 2


def chart(html: str, schema: str) -> str:
    two = frame(html, 2)
    start = two.index(f'<div class="sl-block sl-chartbox {schema}">')
    return two[start : two.index('<p class="sl-fire-line">', start)]


def test_top_rules_left_the_slides_but_not_the_summary() -> None:
    summary = charted()
    assert "Top rules" not in slides(summary)
    whole = render_summary_sections(summary, rule_link=link)
    assert "Flagged by rule" in whole[: whole.index(">Presentation</h3>")]


def test_each_schema_has_its_own_chart_with_two_series_and_a_legend() -> None:
    html = slides(charted())
    for schema in ("v1", "v2"):
        one = chart(html, schema)
        assert f"{schema}: distinct alerts by UTC day</h5>" in one
        legend = one[one.index('<ul class="sl-chart-legend">') : one.index("</ul>")]
        assert "Distinct alerts" in legend and "Rule-flagged (noisy)" in legend
        assert one.count('<path class="sl-line') == 2
        assert one.count('<circle class="sl-pt sl-s1') == 7
        assert one.count('<circle class="sl-pt sl-s2') == 7
        assert "Mon 21" in one and "Sun 27" in one
    assert "<polyline" not in html, "lines are paths; the portal pins its polyline count"
    assert "per day" not in html and "model" not in frame(html, 2).split("sl-bottom")[0]


def test_only_the_last_value_of_each_line_is_labelled() -> None:
    v1 = chart(slides(charted()), "v1")
    labels = re.findall(r'<text class="sl-end"[^>]*>([\d,]+)</text>', v1)
    assert labels == ["3", "2"], "distinct, then rule-flagged, on the last day"


def end_label_ys(svg: str) -> list[float]:
    return [float(y) for y in re.findall(r'<text class="sl-end" x="[\d.]+" y="([\d.]+)"', svg)]


def test_end_labels_stay_clear_of_the_baseline_and_of_each_other() -> None:
    base = CHART_H - 34  # the plot's baseline: chart height minus the bottom margin
    for distinct, flagged in ((0, 0), (1, 0), (5, 5), (9, 1)):
        points = week("v1", [9, 9, distinct], [1, 1, flagged])
        upper, lower = end_label_ys(chart(slides(with_daily(build_summary(), points)), "v1"))
        assert lower <= base - 4, (distinct, flagged, lower)
        assert lower - upper >= 22, "18px labels never overlap"


def test_the_axis_starts_at_zero_with_three_or_four_gridlines() -> None:
    for values in ([3], [7], [12], [1234], [0, 1]):
        points = week("v1", values, [0] * len(values))
        one = chart(slides(with_daily(build_summary(), points)), "v1")
        ticks = re.findall(r'<text class="sl-tick" x="62" [^>]*>([\d,]+)</text>', one)
        assert ticks[0] == "0" and 3 <= len(ticks) <= 4, (values, ticks)
        assert one.count('<line class="sl-grid"') == len(ticks)


def test_overlapping_lines_both_stay_visible() -> None:
    """Rule-flagged often equals distinct: distinct is drawn first and wide, flagged on top,
    thin and dashed, with a smaller marker, and the legend shows the same dash."""
    same = week("v1", [4, 4, 4], [4, 4, 4])
    v1 = chart(slides(with_daily(build_summary(), same)), "v1")
    paths = re.findall(r'<path class="sl-line (sl-s\d)"', v1)
    assert paths == ["sl-s1", "sl-s2"], "distinct first, rule-flagged on top"
    assert v1.index('<circle class="sl-pt sl-s1') < v1.index('<path class="sl-line sl-s2"')
    assert set(re.findall(r'<circle class="sl-pt sl-s1[^"]*"[^>]* r="(\d+)"', v1)) == {"6"}
    assert set(re.findall(r'<circle class="sl-pt sl-s2[^"]*"[^>]* r="(\d+)"', v1)) == {"4"}
    legend = v1[v1.index('<ul class="sl-chart-legend">') : v1.index("</ul>")]
    assert '<path class="sl-key-l sl-s1"' in legend and '<path class="sl-key-l sl-s2"' in legend
    css = STYLESHEET[STYLESHEET.index("Presentation slides") :]
    wide = css[css.index(".sl-line.sl-s1,.sl-key-l.sl-s1{") :]
    assert "stroke-width:4" in wide[: wide.index("}")]
    dashed = css[css.index(".sl-line.sl-s2,.sl-key-l.sl-s2{") :]
    assert "stroke-width:2" in dashed[: dashed.index("}")]
    assert "stroke-dasharray:6 4" in dashed[: dashed.index("}")]


def test_a_partial_day_is_hollow_and_footnoted() -> None:
    full = slides(charted())
    assert "sl-hollow" not in full and PARTIAL_NOTE not in full

    partial = week(
        "v1", [1, 2, 3, 3, 3, 3, 3, 1], [0, 1, 1, 1, 1, 1, 1, 0], [6] + [24.0] * 6 + [18]
    )
    html = slides(with_daily(build_summary(), partial + V2_WEEK))
    v1 = chart(html, "v1")
    assert v1.count("sl-hollow") == 4, "first and last day, on both lines"
    assert frame(html, 2).count(PARTIAL_NOTE) == 1
    assert (
        PARTIAL_NOTE == "Hollow points are partial days (the week does not start at midnight UTC)."
    )


def test_the_partial_day_footnote_follows_only_the_charts_drawn() -> None:
    quiet_partial = week("v1", [0, 0, 0], [0, 0, 0], [6.0, 24.0, 18.0])
    html = slides(with_daily(build_summary(), quiet_partial + V2_WEEK))
    assert "No v1 alerts this week" in chart(html, "v1")
    assert PARTIAL_NOTE not in html, "no hollow point is drawn, so nothing to explain"


def test_a_schema_with_no_rows_says_so_in_the_charts_place() -> None:
    html = slides(with_daily(build_summary(), V1_WEEK))
    v2 = chart(html, "v2")
    assert '<p class="sl-chart-empty">No v2 alerts this week</p>' in v2
    assert "<svg" not in v2 and "sl-chart-legend" not in v2, "no legend over an empty box"
    assert "<svg" in chart(html, "v1") and "sl-chart-legend" in chart(html, "v1")


def test_the_charts_never_combine_v1_and_v2() -> None:
    html = slides(charted())
    v1, v2 = chart(html, "v1"), chart(html, "v2")
    assert "v2" not in v1.replace('class="sl-block sl-chartbox v1"', "")
    assert "v1" not in v2.replace('class="sl-block sl-chartbox v2"', "")
    assert ">6<" not in v1 and ">4<" not in v1, "no day of v1 + v2 distinct alerts"


def test_not_consumed_by_your_dashboards_leads_with_filtered_alerts() -> None:
    rules = (*with_rules(build_summary(), ()).inputs.rules, RuleTotal("v1", "R5", 120, 4))
    nc = block(
        frame(slides(with_rules(build_summary(), rules)), 2), "Not consumed by your dashboards"
    )
    v1 = nc[nc.index('"sl-chip v1"') : nc.index('"sl-chip v2"')]
    assert '<span class="sl-chip v1">v1</span><b>4</b></p>' in nc
    assert '<p class="sl-nc-u">alerts filtered out by your panel SQL</p>' in v1
    assert "<p>120 events</p>" in v1 and "<p>1 alert on no dashboard</p>" in v1
    assert nc.index('"sl-chip v1"') < nc.index('"sl-chip v2"'), "v1 then v2"
    assert "124" not in nc and "4,321" not in nc, "v1 and v2 are never added"


def test_not_consumed_never_turns_a_missing_dashboard_into_zero() -> None:
    """No panel for a schema (unseen is None) is unmeasured, not zero (design 3.2)."""
    rules = (RuleTotal("v1", "R5", 120, 4),)
    nc = block(
        frame(slides(with_rules(build_summary(), rules)), 2), "Not consumed by your dashboards"
    )
    v2 = nc[nc.index('"sl-chip v2"') :]
    assert '<span class="sl-chip v2">v2</span><b>—</b></p>' in nc
    assert '<p class="sl-na">not measured this week</p>' in v2
    assert "<b>0</b>" not in v2 and "events" not in v2 and "filtered out" not in v2

    no_panel = {
        "v1": schema_totals("v1", unseen=None, unseen_alerts=None),
        "v2": schema_totals("v2"),
    }
    nc = block(frame(slides(build_summary(schemas=no_panel)), 2), "Not consumed by your dashboards")
    v1 = nc[nc.index('"sl-chip v1"') : nc.index('"sl-chip v2"')]
    assert "<b>—</b>" in v1 and "not measured this week" in v1 and "events" not in v1
    assert "dashboard supplied" not in slides(build_summary(schemas=no_panel))


def test_not_consumed_reads_the_r5_alerts_of_its_own_schema_only() -> None:
    rules = (
        RuleTotal("v1", "R5", 900, 7),
        RuleTotal("v2", "R5", 30, 2),
        RuleTotal("v1", "R1", 5, 3),
    )
    with_panel = {"v1": schema_totals("v1"), "v2": schema_totals("v2", unseen=0, unseen_alerts=0)}
    summary = with_rules(build_summary(schemas=with_panel), rules)
    nc = block(frame(slides(summary), 2), "Not consumed by your dashboards")
    assert '<span class="sl-chip v1">v1</span><b>7</b></p>' in nc
    assert '<span class="sl-chip v2">v2</span><b>2</b></p>' in nc
    assert "<b>9</b>" not in nc and "<b>10</b>" not in nc
    assert "<p>0 alerts on no dashboard</p>" in nc


def test_one_filtered_alert_reads_in_the_singular() -> None:
    rules = (RuleTotal("v1", "R5", 120, 1),)
    nc = block(
        frame(slides(with_rules(build_summary(), rules)), 2), "Not consumed by your dashboards"
    )
    assert '<p class="sl-nc-u">alert filtered out by your panel SQL</p>' in nc
    assert "alerts filtered out" not in nc


def test_noisiest_applications_keep_rule_and_model_figures_apart() -> None:
    apps = block(frame(slides(), 2), "Noisiest applications")
    loader = apps[apps.index("etl-loader") :]
    assert "<b>2</b> rule-flagged · <b>0</b> model (advisory)" in loader
    assert '<span class="sl-ae">984 events</span>' in loader
    sync = apps[apps.index("warehouse-sync") :]
    assert "<b>0</b> rule-flagged · <b>1</b> model (advisory)" in sync
    assert apps.count("<li>") == 3


def test_firing_patterns_are_one_line_per_schema_from_the_stored_pattern() -> None:
    extra = (
        alert(key_field="s1", fire_pattern="spamming"),
        alert(key_field="s2", fire_pattern="spamming"),
        alert(schema="v2", key_field="f1", fire_pattern="flapping"),
    )
    two = frame(slides(build_summary(alerts=(*ALERTS, *extra))), 2)
    assert "v1: 1 stuck · 2 spamming · 0 flapping</p>" in two
    assert "v2: 0 stuck · 0 spamming · 1 flapping</p>" in two
    v1_box = two[two.index("sl-chartbox v1") : two.index("sl-chartbox v2")]
    assert "Firing patterns · v1: 1 stuck" in v1_box and "v2: 0 stuck" not in v1_box


def test_the_estimate_with_a_projected_week() -> None:
    time = block(frame(slides(), 2), "Time to finish phase 1")
    assert "week of 12 Oct 2026" in time
    assert "2 v1 alert rules left" in time
    assert "≈ 1 working day (0.2 weeks) at 0.5 days per rule, configured" in time
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
