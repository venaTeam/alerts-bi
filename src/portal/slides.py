"""Two ready-made 16:9 presentation slides at the bottom of the team Summary.

The standardization team presents each team's week in PowerPoint. Each frame here is exactly
1280 x 720 CSS px, so a screenshot of it pastes as a slide with nothing to rearrange. Slide 1
says where the team stands; slide 2 says what to fix.

Same rules as the rest of the Summary (:mod:`src.portal.summary_view`): built only from the
:class:`~src.insights.TeamSummary` already computed, every value escaped through
:func:`~src.portal.pages.h`, no script, no inline ``style`` (bars are SVG geometry coloured by
CSS classes), no link, and v1 and v2 never added together. Frames always use a light palette,
even in dark mode, because they are made to be projected; the stylesheet scopes those colours
to ``.slide``. Lists are capped and padded so the layout holds still from week to week, and
long text is cut rather than allowed to spill out of the frame.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from src.insights import AlertRow, AppRow, Estimate, RuleTotal, SchemaTotals, TeamSummary
from src.portal.explain import EN_DASH, rule_explanation
from src.portal.pages import PHASE_STEPS, SCHEMA_NAMES, h
from src.rules.catalogs import CORE_RULE_IDS

__all__ = ["percent", "render_slides"]

SCHEMAS = ("v1", "v2")
#: Caps, so a busy week cannot push content out of the frame.
TOP_FINDINGS = 3
TOP_RULES = 5
TOP_APPLICATIONS = 3
MESSAGE_LIMIT = 90
#: The R6 firing patterns, in the order the slide lists them.
PATTERNS = ("stuck", "spamming", "flapping")
ELLIPSIS = "…"
DASH = "—"

#: The examined split: always these three, then the other states only when non-zero.
_SPLIT = (
    ("rule_flagged", "sl-q-rule", "rule-flagged", True),
    ("llm_flagged", "sl-q-model", "model-flagged (advisory)", True),
    ("assessed_good", "sl-q-good", "assessed good", True),
    ("needs_review", "sl-q-review", "needs a decision", False),
    ("unassessed", "sl-q-un", "not reviewed", False),
)


# ------------------------------------------------------------------ small helpers


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _clip(text: str, limit: int) -> str:
    """At most ``limit`` characters, the last one an ellipsis when anything was cut."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + ELLIPSIS


def percent(part: float, whole: float) -> str:
    """``part`` as a share of ``whole``, the one way every percentage on the slides is written.

    Rounds half up exactly (``12.5`` is ``13%``), never to even. A share that is not nothing
    never reads ``0%``: it reads ``<1%``. And only the whole reads ``100%``: anything short
    of it stops at ``99%``, so a team 99.6% ready is not shown as done.
    """
    if whole <= 0 or part <= 0:
        return "0%"
    if part >= whole:
        return "100%"
    share = Decimal(str(part)) * 100 / Decimal(str(whole))
    rounded = int(share.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if rounded < 1:
        return "<1%"
    return f"{min(rounded, 99)}%"


def _day(moment: datetime | date) -> str:
    """``28 Sep 2026``."""
    return f"{moment.day} {moment:%b %Y}"


def _chip(schema: str) -> str:
    return f'<span class="sl-chip {h(schema)}">{h(schema)}</span>'


def _phase_label(phase: str) -> str:
    for key, label in PHASE_STEPS:
        if key == phase:
            return label
    return "No alerts this week" if phase == "no_data" else phase


def _block(css: str, title: str, body: str) -> str:
    return f'<div class="sl-block {css}"><h5 class="sl-label">{h(title)}</h5>{body}</div>'


def _none() -> str:
    return '<p class="sl-none">None this week</p>'


def _footer(summary: TeamSummary, number: int) -> str:
    inputs = summary.inputs
    return (
        '<footer class="sl-foot">'
        f"<span>{h(inputs.display_name)} · week ending {h(_day(inputs.window_end))} · "
        "Alerts BI</span>"
        f'<span class="sl-page">{number} / 2</span></footer>'
    )


def _frame(number: int, title: str, head_extra: str, body: str, summary: TeamSummary) -> str:
    return (
        f'<section class="slide sl-{number}" aria-label="Slide {number}: {h(title)}">'
        f'<header class="sl-head"><h4 class="sl-title">{h(title)}</h4>{head_extra}</header>'
        f"{body}{_footer(summary, number)}</section>"
    )


# ------------------------------------------------------------------ slide 1


def _split_bar(totals: SchemaTotals) -> str:
    shown = [
        (css, label, int(totals.states.get(state, 0)))
        for state, css, label, always in _SPLIT
        if always or totals.states.get(state, 0)
    ]
    total = sum(count for _, _, count in shown)
    rects, offset = [], 0.0
    for css, label, count in shown:
        if not count:
            continue
        width = 100 * count / total
        rects.append(
            f'<rect class="{css}" x="{offset:.3f}" y="0" width="{max(width - 0.3, 0.2):.3f}" '
            f'height="4"><title>{h(label)}: {count:,}</title></rect>'
        )
        offset += width
    legend = "".join(
        f'<li><span class="sl-sw {css}" aria-hidden="true"></span>{h(label)} <b>{count:,}</b></li>'
        for css, label, count in shown
    )
    return (
        f'<svg class="sl-bar" viewBox="0 0 100 4" preserveAspectRatio="none" role="img" '
        f'aria-label="{h(totals.schema)} alerts by review outcome">'
        f'<rect class="sl-track" x="0" y="0" width="100" height="4"/>{"".join(rects)}</svg>'
        f'<ul class="sl-legend">{legend}</ul>'
    )


def _schema_panel(totals: SchemaTotals, schema: str) -> str:
    heading = f'<h5 class="sl-schema-h">{_chip(schema)}{h(SCHEMA_NAMES[schema])}</h5>'
    if totals.events == 0 and totals.distinct_alerts == 0:
        return (
            f'<div class="sl-schema {schema} sl-empty">{heading}'
            f'<p class="sl-none">No {h(schema)} alerts this week</p></div>'
        )
    flagged = totals.rule_flagged_alerts
    return (
        f'<div class="sl-schema {schema}">{heading}'
        '<div class="sl-kpi">'
        f'<p class="sl-big"><b>{totals.distinct_alerts:,}</b>'
        f"<span>{'alert' if totals.distinct_alerts == 1 else 'alerts'}</span></p>"
        '<ul class="sl-facts">'
        f"<li>{_plural(totals.events, 'event')}</li>"
        f"<li>{flagged:,} rule-flagged {'alert' if flagged == 1 else 'alerts'} "
        f"({h(percent(totals.rule_flagged_events, totals.events))} of events)</li>"
        "</ul></div>"
        f"{_split_bar(totals)}</div>"
    )


def _findings(summary: TeamSummary) -> str:
    findings = summary.key_findings[:TOP_FINDINGS]
    if not findings:
        return _block("sl-kf", "Key findings", _none())
    items = [
        f"<li><b>{h(finding.title)}</b> <span>{h(finding.body)}</span></li>" for finding in findings
    ]
    items += ['<li class="sl-pad">' + DASH + "</li>"] * (TOP_FINDINGS - len(items))
    return _block("sl-kf", "Key findings", f'<ol class="sl-lines">{"".join(items)}</ol>')


def _biggest(summary: TeamSummary) -> str:
    alert: AlertRow | None = summary.biggest
    if alert is None:
        return _block("sl-big1", "Biggest single source", _none())
    schema_totals = summary.inputs.schemas.get(alert.schema)
    total = max(schema_totals.events if schema_totals else 0, alert.row_count)
    message = _clip(alert.message or "(no message)", MESSAGE_LIMIT)
    return _block(
        "sl-big1",
        "Biggest single source",
        f'<p class="sl-lead">1 alert produced <b>{alert.row_count:,}</b> of {total:,} '
        f"{h(alert.schema)} events ({h(percent(alert.row_count, total))})</p>"
        f'<p class="sl-app">{_chip(alert.schema)}<span class="sl-app-n">{h(alert.application)}</span></p>'
        f'<p class="sl-msg">“{h(message)}”</p>',
    )


def _slide_one(summary: TeamSummary) -> str:
    inputs = summary.inputs
    start, end = inputs.window_start, inputs.window_end
    # A week with no alerts has no readiness to speak of, whatever the stored value.
    readiness = (
        f"Phase-2 readiness: {DASH}"
        if inputs.phase2_readiness_pct is None or inputs.phase == "no_data"
        else f"{percent(inputs.phase2_readiness_pct, 100)} phase-2 ready"
    )
    subtitle = (
        '<p class="sl-sub">'
        f"<span>Week of {start.day} {start:%b} {EN_DASH} {h(_day(end))} (UTC)</span>"
        f'<span class="sl-phase">{h(_phase_label(inputs.phase))}</span>'
        f"<span>{h(readiness)}</span></p>"
    )
    panels = "".join(_schema_panel(inputs.schemas[schema], schema) for schema in SCHEMAS)
    body = (
        f'<div class="sl-schemas">{panels}</div>'
        f'<div class="sl-row">{_findings(summary)}{_biggest(summary)}</div>'
    )
    return _frame(1, f"Where {inputs.display_name} stands", subtitle, body, summary)


# ------------------------------------------------------------------ slide 2


def _rule_order(rule_id: str) -> int:
    return CORE_RULE_IDS.index(rule_id) if rule_id in CORE_RULE_IDS else len(CORE_RULE_IDS)


def _top_rules(rules: Sequence[RuleTotal]) -> list[RuleTotal]:
    """Up to five core rule rows (R1-R7, R6 included) by events; one row per rule and schema."""
    core = [rule for rule in rules if rule.rule_id in CORE_RULE_IDS and rule.events > 0]
    core.sort(key=lambda r: (-r.events, -r.alerts, r.schema, _rule_order(r.rule_id)))
    return core[:TOP_RULES]


def _rules(summary: TeamSummary) -> str:
    rows = _top_rules(summary.inputs.rules)
    if not rows:
        return _block("sl-rules", "Top rules", _none())
    items = []
    for rule in rows:
        text = rule_explanation(rule.rule_id, None)
        title = "" if text.title == rule.rule_id else text.title
        items.append(
            "<li>"
            f'<span class="sl-rid">{h(rule.rule_id)}</span>{_chip(rule.schema)}'
            f'<span class="sl-rt">{h(title)}</span>'
            f'<span class="sl-n">{_plural(rule.alerts, "alert")} · {rule.events:,} events</span>'
            f'<span class="sl-step">{h(text.next_step)}</span>'
            "</li>"
        )
    items += ['<li class="sl-pad">' + DASH + "</li>"] * (TOP_RULES - len(items))
    return _block("sl-rules", "Top rules", f'<ol class="sl-rule-list">{"".join(items)}</ol>')


def _app(row: AppRow) -> str:
    return (
        "<li>"
        f'<span class="sl-an">{h(row.application)}</span>{_chip(row.schema)}'
        f'<span class="sl-af"><b>{row.rule_flagged_alerts:,}</b> rule-flagged · '
        f"<b>{row.llm_flagged_alerts:,}</b> model (advisory) · {_plural(row.events, 'event')}"
        "</span></li>"
    )


def _applications(summary: TeamSummary) -> str:
    apps = summary.by_application[:TOP_APPLICATIONS]
    if not apps:
        return _block("sl-apps", "Noisiest applications", _none())
    items = [_app(row) for row in apps]
    items += ['<li class="sl-pad">' + DASH + "</li>"] * (TOP_APPLICATIONS - len(items))
    return _block(
        "sl-apps", "Noisiest applications", f'<ol class="sl-app-list">{"".join(items)}</ol>'
    )


def _patterns(summary: TeamSummary) -> str:
    counts = {
        (schema, pattern): sum(
            1 for a in summary.inputs.alerts if a.schema == schema and a.fire_pattern == pattern
        )
        for schema in SCHEMAS
        for pattern in PATTERNS
    }
    head = "".join(f'<th scope="col">{_chip(schema)}</th>' for schema in SCHEMAS)
    rows = "".join(
        f'<tr><th scope="row">{pattern}</th>'
        + "".join(f"<td>{counts[(schema, pattern)]:,}</td>" for schema in SCHEMAS)
        + "</tr>"
        for pattern in PATTERNS
    )
    return _block(
        "sl-fire",
        "Firing patterns",
        f'<table class="sl-table"><thead><tr><th scope="col">alerts</th>{head}</tr></thead>'
        f"<tbody>{rows}</tbody></table>",
    )


def _out_of_view(summary: TeamSummary) -> str:
    lines = []
    for schema in SCHEMAS:
        totals = summary.inputs.schemas[schema]
        if totals.suppressed == 0 and totals.unseen is None:
            facts = '<span class="sl-na">no dashboard supplied</span>'
        else:
            unseen = (
                '<span class="sl-na">no dashboard supplied</span>'
                if totals.unseen_alerts is None
                else f"{_plural(totals.unseen_alerts, 'alert')} on no dashboard"
            )
            facts = (
                f"<span>{_plural(totals.suppressed, 'event')} hidden by your own panels</span>"
                f"<span>{unseen}</span>"
            )
        lines.append(f'<li>{_chip(schema)}<span class="sl-ov">{facts}</span></li>')
    return _block("sl-view", "Out of view", f'<ul class="sl-view-list">{"".join(lines)}</ul>')


def _g(value: float) -> str:
    return f"{value:g}"


def _time_left(estimate: Estimate) -> str:
    if estimate.projected_week_end is not None:
        when = f'<p class="sl-when">week of {h(_day(estimate.projected_week_end))}</p>'
    else:
        when = (
            f'<p class="sl-noest"><b>No estimate:</b> {h(estimate.no_estimate_reason or DASH)}</p>'
        )
    return _block(
        "sl-time",
        "Time to finish phase 1",
        f"{when}"
        f"<p>{h(_plural(estimate.rules_left, 'v1 alert rule'))} left</p>"
        f"<p>≈ {_g(estimate.effort_days)} working {'day' if estimate.effort_days == 1 else 'days'} "
        f"({estimate.effort_weeks:.1f} weeks) at "
        f"{_g(estimate.effort_days_per_rule)} days per rule, configured</p>"
        '<p class="sl-note">A projection. v1 falling may be cleanup rather than migration.</p>',
    )


def _slide_two(summary: TeamSummary) -> str:
    body = (
        f'<div class="sl-col sl-left">{_rules(summary)}{_time_left(summary.estimate)}</div>'
        f'<div class="sl-col sl-right">{_applications(summary)}{_patterns(summary)}'
        f"{_out_of_view(summary)}</div>"
    )
    return _frame(2, "What to fix", "", body, summary)


# ------------------------------------------------------------------ the section


def render_slides(summary: TeamSummary) -> str:
    """The "Presentation" section: two fixed 1280 x 720 frames, ready to screenshot."""
    return (
        '<section class="slides" aria-labelledby="slides-h">'
        '<div class="sl-intro"><h3 id="slides-h">Presentation</h3>'
        "<p>Two 16:9 slides for this week. Screenshot each frame and paste it as a slide.</p>"
        "</div>"
        f'<div class="sl-scroll">{_slide_one(summary)}{_slide_two(summary)}</div>'
        "</section>"
    )
