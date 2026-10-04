"""A team's published week, one tab per question (product owner, 2026-10-04).

Overview says where the team stands; Fix list says what to change and lists every alert;
Volume, Dashboards, Migration and History answer one question each; Slides holds the two
presentation frames. The alert page opens from the Fix list.

The copy names problems in plain words and never shows a rule id: ids travel only in the
query strings of filter links. Text stays short: a label, a number, one sentence to fix.

The portal's rules hold on every tab: weekly totals, never a per-day rate; no run id or
version; v1 and v2 never added together; no script and no inline ``style``; every alert value
escaped through :func:`~src.portal.pages.h`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from src.insights import AlertRow as SummaryAlert
from src.insights import RuleTotal, TeamSummary
from src.insights.labels import STATE_LABELS, action, fix, problem, why
from src.portal.charts import ChartPoint, Segment, line_chart, stacked_bar
from src.portal.explain import (
    decision_question,
    dominant_r6_pattern,
    format_instant,
    format_week,
    principle_next_step,
    principle_title,
    rule_explanation,
)
from src.portal.pages import (
    PHASE_STEPS,
    SCHEMA_NAMES,
    TABS,
    alert_url,
    h,
    layout,
    link,
    phase_label,
    plural,
    tab_url,
)
from src.portal.queries import AlertDetail, AlertRow, Decision, Review, WorklistPage
from src.portal.slides import render_slides
from src.rules.catalogs import (
    CORE_RULE_IDS,
    R6_API_SPAM_PER_24H,
    R6_FLAP_CYCLES,
    R6_STUCK_OPEN,
    V2_READINESS_RULE_IDS,
)

__all__ = [
    "alert_page",
    "dashboards_page",
    "fix_page",
    "history_page",
    "migration_page",
    "overview_page",
    "slides_page",
    "volume_page",
]

SCHEMAS: Final = ("v1", "v2")
#: How many rows the short lists show; the Fix list has every alert.
TOP_LOUD: Final = 4
TOP_LISTED: Final = 8
#: Phase-2 readiness, in the order a team should work through it: the gap that blocks
#: completion first.
READINESS_ORDER: Final = ("R9", "R8", "R10")
PHASES: Final[dict[str, tuple[str, str]]] = {
    "phase_0": ("Phase 0 · Clean up", "Nothing hidden, volume low"),
    "phase_1": ("Phase 1 · New rules", "Done when no v1 alert fires"),
    "phase_2": ("Phase 2 · Enrich", "Critical v2 alerts have impact and runbook"),
    "done": ("Done", "All of the above"),
}
_STATE_CSS: Final = (
    ("rule_flagged", "q-rule"),
    ("llm_flagged", "q-model"),
    ("needs_review", "q-review"),
    ("assessed_good", "q-good"),
    ("unassessed", "q-un"),
)
_CHEVRON: Final = (
    '<svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4"/></svg>'
)


# ------------------------------------------------------------------ small helpers


def _chip(css: str, text: str) -> str:
    return f'<span class="chip {css}">{h(text)}</span>'


def _schema_chip(schema: str) -> str:
    return _chip(schema, schema)


def _alert_href(selected: Review, schema: str, application: str, key: str) -> str:
    return alert_url(selected.team_id, selected.week, schema, application, key)


def _sample(alert: AlertRow, rule_id: str) -> dict[str, Any]:
    entry = alert.evidence.get(rule_id) or {}
    sample = entry.get("sample_evidence")
    return sample if isinstance(sample, dict) else {}


def _pattern(alert: AlertRow) -> str | None:
    pattern = _sample(alert, "R6").get("pattern")
    return pattern if isinstance(pattern, str) else None


def _critical(severity: str | None) -> bool:
    return (severity or "").lower() == "critical"


def _problems(alert: AlertRow) -> list[str]:
    """Every problem chip on one alert: rule findings, the model, then readiness."""
    critical = _critical(alert.severity)
    chips = [
        _chip("rule", problem(rid, _pattern(alert), critical=critical))
        for rid in alert.core_rule_ids
    ]
    if alert.quality_state == "llm_flagged" and alert.llm_principle_id:
        chips.append(_chip("model", STATE_LABELS["llm_flagged"]))
    elif alert.quality_state == "needs_review":
        chips.append(_chip("review", STATE_LABELS["needs_review"]))
    chips.extend(
        _chip("ready", problem(rid, critical=critical)) for rid in alert.readiness_rule_ids
    )
    return chips


def _span(hours: float) -> str:
    if hours < 1:
        return f"{round(hours * 60)} min"
    if hours < 48:
        return f"{hours:,.1f}".removesuffix(".0") + " h"
    return f"{hours / 24:,.1f}".removesuffix(".0") + " days"


def _is_ready(alert: SummaryAlert) -> bool:
    """Phase-2 ready (design section 6): an impact, not a cause, and a runbook if critical."""
    gaps = set(alert.readiness_rule_ids)
    return not ({"R8", "R10"} & gaps) and not (_critical(alert.severity) and "R9" in gaps)


def _quiet(text: str) -> str:
    return f'<p class="quiet">{text}</p>'


def _alert_cell(href: str, message: str | None, application: str, component: str | None) -> str:
    return (
        f'<td><a class="msg" href="{h(href)}">“{h(message or "(no message)")}”</a>'
        f'<div class="src">{h(application)} &rsaquo; {h(component or "—")}</div></td>'
    )


# ------------------------------------------------------------------ the shell


def _week_menu(reviews: Sequence[Review], selected: Review, tab: str) -> str:
    """The week menu: no script, so a list of links that keeps the open tab."""
    items = "".join(
        f'<li><a href="{h(tab_url(r.team_id, r.week, tab))}"'
        f"{' aria-current="page"' if r.run_id == selected.run_id else ''}>"
        f"{h(format_week(r.window_start, r.window_end))}</a></li>"
        for r in reversed(reviews)
    )
    return (
        '<div class="wk"><span class="sub">Week</span><details class="menu">'
        f"<summary>{h(format_week(selected.window_start, selected.window_end))}{_CHEVRON}"
        f'</summary><ul class="menu-list">{items}</ul></details></div>'
    )


def _tabs(selected: Review, active: str) -> str:
    links = []
    for key, label, _ in TABS:
        count = selected.needs_attention if key == "fix" else 0
        badge = f' <span class="cnt">{count:,}</span>' if count else ""
        current = ' aria-current="page"' if key == active else ""
        links.append(
            f'<a class="tab{" on" if key == active else ""}" '
            f'href="{h(tab_url(selected.team_id, selected.week, key))}"{current}>'
            f"{label}{badge}</a>"
        )
    return f'<nav class="tabs" aria-label="This week">{"".join(links)}</nav>'


def _page(
    reviews: Sequence[Review], selected: Review, active: str, body: str, *, crumb: str = ""
) -> str:
    label = {key: text for key, text, _ in TABS}[active]
    crumbs = (
        '<nav class="crumbs" aria-label="Breadcrumb"><a href="/">All teams</a><span>/</span>'
        f"<span>{h(selected.team_name)}</span>"
        + (f"<span>/</span><span>{h(crumb)}</span>" if crumb else "")
        + "</nav>"
    )
    head = (
        f'<section class="head"><h1>{h(selected.team_name)}</h1>'
        f"{_week_menu(reviews, selected, active)}</section>"
    )
    title = (
        f"{selected.team_name} · {crumb or label} · "
        f"{format_week(selected.window_start, selected.window_end)}"
    )
    return layout(title, crumbs + head + _tabs(selected, active) + body)


# ------------------------------------------------------------------ overview


def _schema_card(summary: TeamSummary, schema: str) -> str:
    totals = summary.inputs.schemas[schema]
    name = SCHEMA_NAMES[schema]
    if totals.events == 0 and totals.distinct_alerts == 0:
        body = f'<p class="sub">No {schema} alerts this week.</p>'
    else:
        segments = [
            Segment(css, STATE_LABELS[state], int(totals.states.get(state, 0)))
            for state, css in _STATE_CSS
        ]
        body = (
            '<div class="kp">'
            f'<div><span class="big">{totals.distinct_alerts:,}</span>'
            '<span class="sub">alerts</span></div>'
            f'<div><span class="big">{totals.events:,}</span><span class="sub">events</span></div>'
            "</div>"
            f"<div>{stacked_bar(segments, label=f'{name}: review outcome per alert')}</div>"
        )
    return f'<article class="card pad stack {schema}"><h2>{name}</h2>{body}</article>'


def _finding_target(selected: Review, kind: str, rule_filter: str | None) -> tuple[str, str]:
    team, week = selected.team_id, selected.week
    if kind in ("hidden", "unseen"):
        return tab_url(team, week, "dashboards"), "Dashboards"
    if kind == "readiness":
        return tab_url(team, week, "migration"), "Migration"
    if kind == "unassessed":
        return tab_url(team, week, "fix", state="unassessed", show="all"), "Show alerts"
    return tab_url(team, week, "fix", rule=rule_filter), "Show alerts"


def _findings(selected: Review, summary: TeamSummary) -> str:
    if not summary.key_findings:
        return _quiet("Nothing stands out this week.")
    items = []
    for number, finding in enumerate(summary.key_findings, start=1):
        href, text = _finding_target(selected, finding.kind, finding.rule_filter)
        items.append(
            f'<li><span class="n1">{number}</span><div class="col">'
            f'<b>{h(finding.title)}</b><span class="sub">{h(finding.body)}</span>'
            f'<a href="{h(href)}">{text}</a></div></li>'
        )
    return f'<ol class="findings">{"".join(items)}</ol>'


def _phase_card(selected: Review, summary: TeamSummary) -> str:
    steps = "".join(
        f"<li{' class="on"' if key == selected.phase else ''}>{h(PHASES[key][0])}</li>"
        for key, _ in PHASE_STEPS
    )
    v2 = [a for a in summary.inputs.alerts if a.schema == "v2"]
    ready = f"{sum(_is_ready(a) for a in v2):,} of {len(v2):,}" if v2 else "—"
    none = '<p class="sub">No alerts this week.</p>' if selected.phase == "no_data" else ""
    return (
        '<section class="card pad stack"><div class="eyebrow">Phase</div>'
        f'<ol class="vsteps">{steps}</ol>{none}'
        '<dl class="tot">'
        f"<dt>v1 alert rules left</dt><dd>{summary.estimate.rules_left:,}</dd>"
        f"<dt>v2 ready</dt><dd>{ready}</dd></dl>"
        f'<a href="{h(tab_url(selected.team_id, selected.week, "migration"))}">Details</a>'
        "</section>"
    )


def _loudest(selected: Review, summary: TeamSummary) -> str:
    alert = summary.biggest
    if alert is None:
        return ""
    href = _alert_href(selected, alert.schema, alert.application, alert.key_field)
    return (
        '<section class="card pad stack tight"><div class="eyebrow">Loudest alert</div>'
        f'<a class="msg" href="{h(href)}">“{h(alert.message or "(no message)")}”</a>'
        f'<span class="src">{h(alert.application)} &rsaquo; {h(alert.component or "—")} '
        f"{_schema_chip(alert.schema)}</span>"
        f'<span class="num"><b>{alert.row_count:,}</b> events</span></section>'
    )


def overview_page(reviews: Sequence[Review], selected: Review, summary: TeamSummary) -> str:
    note = (
        '<section class="note"><div class="eyebrow">Note</div>'
        f"<p>{h(selected.review_note)}</p></section>"
        if selected.review_note
        else ""
    )
    attention = selected.needs_attention
    cta = (
        f'<a class="button primary" href="{h(tab_url(selected.team_id, selected.week, "fix"))}">'
        f"Open fix list ({attention:,})</a>"
        if attention
        else ""
    )
    body = (
        f"{note}"
        '<div class="split"><div class="colmain">'
        f'<div class="two">{_schema_card(summary, "v1")}{_schema_card(summary, "v2")}</div>'
        f'<section class="card pad stack"><h2>Key findings</h2>{_findings(selected, summary)}'
        "</section></div>"
        f'<aside class="colside">{_phase_card(selected, summary)}{_loudest(selected, summary)}'
        f"{cta}</aside></div>"
    )
    return _page(reviews, selected, "overview", body)


# ------------------------------------------------------------------ fix list


def _fix_href(
    selected: Review, *, show: str, schema: str, state: str, rule: str, page: int | None = None
) -> str:
    return (
        tab_url(
            selected.team_id,
            selected.week,
            "fix",
            show=None if show == "attention" else show,
            schema=None if schema == "all" else schema,
            state=None if state == "all" else state,
            rule=rule or None,
            page=page,
        )
        + "#alerts"
    )


def _by_rule(rules: Iterable[RuleTotal]) -> dict[str, dict[str, RuleTotal]]:
    grouped: dict[str, dict[str, RuleTotal]] = defaultdict(dict)
    for total in rules:
        grouped[total.rule_id][total.schema] = total
    return grouped


def _cell(total: RuleTotal | None, *, events: bool = True) -> str:
    if total is None or total.alerts == 0:
        return '<td class="num r"><span class="sub">—</span></td>'
    extra = f'<span class="sub">{plural(total.events, "event")}</span>' if events else ""
    return f'<td class="num r">{plural(total.alerts, "alert")}{extra}</td>'


def _r6_pattern(summary: TeamSummary) -> str | None:
    return dominant_r6_pattern(
        (a.fire_pattern, a.row_count) for a in summary.inputs.alerts if "R6" in a.core_rule_ids
    )


def _changes(selected: Review, summary: TeamSummary) -> str:
    """What to change: one row per problem, per-schema counts side by side, never summed."""
    grouped = _by_rule(summary.inputs.rules)
    order = {rid: index for index, rid in enumerate(CORE_RULE_IDS)}
    quality = sorted(
        (rid for rid in grouped if rid in CORE_RULE_IDS),
        key=lambda rid: (-max(t.events for t in grouped[rid].values()), order[rid]),
    )
    critical = sum(
        1
        for a in summary.inputs.alerts
        if a.schema == "v2" and _critical(a.severity) and "R9" in a.readiness_rule_ids
    )

    def row(rid: str, label: str, *, events: bool = True) -> str:
        href = tab_url(selected.team_id, selected.week, "fix", rule=rid) + "#alerts"
        return (
            f'<tr><td><span class="item">{h(label)}</span></td>'
            f"{_cell(grouped[rid].get('v1'), events=events)}"
            f"{_cell(grouped[rid].get('v2'), events=events)}"
            f'<td class="r"><a href="{h(href)}">Show</a></td></tr>'
        )

    def nothing(text: str = "Nothing this week.") -> str:
        return f'<tr><td colspan="4" class="sub">{text}</td></tr>'

    rows = ['<tr class="grp g-fix"><td colspan="4">Fix or delete</td></tr>']
    rows += [row(rid, action(rid)) for rid in quality] or [nothing()]

    rows.append('<tr class="grp g-adv"><td colspan="4">Advisory</td></tr>')
    advisory = []
    for state, label in (("llm_flagged", "Read the advisory findings"), ("needs_review", "Decide")):
        counts = {s: int(summary.inputs.schemas[s].states.get(state, 0)) for s in SCHEMAS}
        if not any(counts.values()):
            continue
        cells = "".join(
            f'<td class="num r">{plural(n, "alert")}</td>'
            if n
            else '<td class="num r"><span class="sub">—</span></td>'
            for n in counts.values()
        )
        href = tab_url(selected.team_id, selected.week, "fix", state=state) + "#alerts"
        advisory.append(
            f'<tr><td><span class="item">{label}</span></td>{cells}'
            f'<td class="r"><a href="{h(href)}">Show</a></td></tr>'
        )
    rows += advisory or [nothing()]

    rows.append('<tr class="grp g-ready"><td colspan="4">Get v2 ready</td></tr>')
    ready = []
    for rid in READINESS_ORDER:
        if rid not in grouped:
            continue
        label = action(rid) + (f" ({critical:,} critical)" if rid == "R9" and critical else "")
        ready.append(row(rid, label, events=False))
    has_v2 = bool(summary.inputs.schemas["v2"].distinct_alerts)
    rows += ready or [nothing("Nothing this week." if has_v2 else "No v2 alerts this week.")]

    return (
        '<section class="card"><div class="card-h"><h2>What to change</h2></div>'
        '<div class="table-wrap"><table class="list"><thead><tr><th scope="col">Change</th>'
        f'<th scope="col" class="r">{_schema_chip("v1")}</th>'
        f'<th scope="col" class="r">{_schema_chip("v2")}</th><th scope="col"></th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _banner(selected: Review, summary: TeamSummary, rule: str) -> str:
    """One problem selected: what it is, the fix, and its alerts per schema."""
    pattern = _r6_pattern(summary) if rule == "R6" else None
    per = _by_rule(summary.inputs.rules).get(rule, {})
    counts = "".join(
        f"<dt>{_schema_chip(s)}</dt><dd>{plural(per[s].alerts, 'alert')} · "
        f"{plural(per[s].events, 'event')}</dd>"
        for s in SCHEMAS
        if s in per and per[s].alerts
    )
    back = tab_url(selected.team_id, selected.week, "fix")
    return (
        f'<p class="back"><a href="{h(back)}">← All changes</a></p>'
        '<section class="card pad banner"><div class="stack tight">'
        f'<h2>{h(action(rule))}</h2><p class="sub">{h(why(rule, pattern))}</p>'
        f'<p class="fixline"><b>Fix:</b> {h(fix(rule, pattern))}</p></div>'
        + (f'<dl class="tot">{counts}</dl>' if counts else "")
        + "</section>"
    )


def _decision_chips(decisions: Mapping[str, Decision]) -> str:
    return "".join(
        f'<span class="chip human {h(d.state)}" title="Human decision">'
        f"{h(d.state.capitalize())}</span>"
        for d in decisions.values()
    )


def _alert_row(selected: Review, alert: AlertRow, decisions: Mapping[str, Decision]) -> str:
    chips = _problems(alert)
    if not chips:
        css = "un" if alert.quality_state == "unassessed" else "good"
        chips.append(_chip(css, STATE_LABELS.get(alert.quality_state, alert.quality_state)))
    href = _alert_href(selected, alert.alert_schema, alert.application, alert.key_field)
    return (
        "<tr>"
        f"{_alert_cell(href, alert.message, alert.application, alert.component)}"
        f"<td>{_schema_chip(alert.alert_schema)}</td>"
        f'<td><div class="why">{"".join(chips)}{_decision_chips(decisions)}</div></td>'
        f'<td class="num r"><b>{alert.row_count:,}</b></td>'
        "</tr>"
    )


def fix_page(
    reviews: Sequence[Review],
    selected: Review,
    summary: TeamSummary,
    listing: WorklistPage,
    decided: Mapping[tuple[str, str, str], Mapping[str, Decision]],
    counts: Mapping[str, int],
    *,
    show: str,
    schema: str,
    state: str,
    rule: str,
) -> str:
    def href(**changes: Any) -> str:
        current: dict[str, Any] = {
            "show": show,
            "schema": schema,
            "state": state,
            "rule": rule,
            **changes,
        }
        return _fix_href(selected, **current)

    def segment(options: Iterable[tuple[str, str, bool]], label: str) -> str:
        links = "".join(
            f'<a href="{h(target)}"{' class="on" aria-current="true"' if on else ""}>{h(text)}</a>'
            for text, target, on in options
        )
        return f'<nav class="seg" aria-label="{label}">{links}</nav>'

    outcome_items = "".join(
        f'<li><a href="{h(href(state=key))}"'
        f"{' aria-current="true"' if key == state else ''}>{h(text)}</a></li>"
        for key, text in (("all", "Any"), *STATE_LABELS.items())
    )
    outcome = (
        '<details class="menu"><summary>Outcome: '
        f"{h('Any' if state == 'all' else STATE_LABELS[state])}{_CHEVRON}</summary>"
        f'<ul class="menu-list">{outcome_items}</ul></details>'
    )
    selected_rule = (
        f'<span class="chip rule">{h(problem(rule))}</span><a href="{h(href(rule=""))}">Clear</a>'
        if rule
        else ""
    )
    filters = (
        '<div class="filters">'
        + segment(
            [
                ("To fix", href(show="attention"), show == "attention"),
                ("All", href(show="all"), show == "all"),
            ],
            "Which alerts",
        )
        + segment(
            [
                ("v1 + v2", href(schema="all"), schema == "all"),
                ("v1", href(schema="v1"), schema == "v1"),
                ("v2", href(schema="v2"), schema == "v2"),
            ],
            "Schema",
        )
        + outcome
        + selected_rule
        + "</div>"
    )

    if listing.rows:
        rows = "".join(
            _alert_row(
                selected,
                alert,
                decided.get((alert.alert_schema, alert.application, alert.key_field), {}),
            )
            for alert in listing.rows
        )
        table = (
            '<div class="table-wrap"><table class="list"><thead><tr>'
            '<th scope="col">Alert</th><th scope="col">Schema</th><th scope="col">Problems</th>'
            f'<th scope="col" class="r">Events</th></tr></thead><tbody>{rows}</tbody></table></div>'
        )
    else:
        clear = tab_url(selected.team_id, selected.week, "fix", show="all") + "#alerts"
        table = _quiet(f'Nothing matches. <a href="{h(clear)}">Clear filters</a>')

    first = (listing.page - 1) * listing.page_size + 1 if listing.total else 0
    last = min(listing.page * listing.page_size, listing.total)

    def page_link(target: int, text: str, enabled: bool) -> str:
        if not enabled:
            return f'<span class="button" aria-disabled="true">{text}</span>'
        return f'<a class="button" href="{h(href(page=target))}">{text}</a>'

    pager = (
        f'<div class="pager"><span>{first}&ndash;{last} of {listing.total:,}</span>'
        + (
            f'<span class="links">{page_link(listing.page - 1, "Previous", listing.page > 1)}'
            f"{page_link(listing.page + 1, 'Next', listing.page < listing.pages)}</span>"
            if listing.pages > 1
            else ""
        )
        + "</div>"
    )
    count = counts["attention"] if show == "attention" else counts["all"]
    top = _banner(selected, summary, rule) if rule else _changes(selected, summary)
    body = (
        f"{top}"
        '<section class="card" id="alerts">'
        f'<div class="card-h"><h2>Alerts <span class="sub">{count:,}</span></h2></div>'
        f"{filters}{table}{pager}</section>"
    )
    return _page(reviews, selected, "fix", body)


# ------------------------------------------------------------------ volume


def _pattern_line(alerts: Iterable[SummaryAlert]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for alert in alerts:
        if alert.fire_pattern:
            counts[alert.fire_pattern] += 1
    return " · ".join(
        f"{n:,} {name}" for name in ("stuck", "spamming", "flapping") if (n := counts[name])
    )


def volume_page(reviews: Sequence[Review], selected: Review, summary: TeamSummary) -> str:
    any_pattern = any(a.fire_pattern for a in summary.inputs.alerts)
    columns = 4 if any_pattern else 3
    rows = []
    for schema in SCHEMAS:
        line = _pattern_line(a for a in summary.inputs.alerts if a.schema == schema)
        extra = f' <span class="sub">· {h(line)}</span>' if line else ""
        rows.append(
            f'<tr class="grp"><td colspan="{columns}">{SCHEMA_NAMES[schema]}{extra}</td></tr>'
        )
        top = [r for r in summary.fire if r.alert.schema == schema][:TOP_LOUD]
        if not top:
            rows.append(
                f'<tr><td colspan="{columns}" class="sub">No {schema} alerts this week.</td></tr>'
            )
        for fire_row in top:
            alert = fire_row.alert
            href = _alert_href(selected, alert.schema, alert.application, alert.key_field)
            pattern = (
                f"<td>{_chip('rule', problem('R6', fire_row.pattern)) if fire_row.pattern else ''}"
                "</td>"
                if any_pattern
                else ""
            )
            rows.append(
                "<tr>"
                f"{_alert_cell(href, alert.message, alert.application, alert.component)}"
                f'<td class="num r"><b>{alert.row_count:,}</b></td>'
                f'<td class="num r">{_span(fire_row.span_hours)}</td>{pattern}</tr>'
            )
    head = (
        '<tr><th scope="col">Alert</th><th scope="col" class="r">Events</th>'
        '<th scope="col" class="r">Active</th>'
        + ('<th scope="col">Pattern</th>' if any_pattern else "")
        + "</tr>"
    )
    stuck_hours = int(R6_STUCK_OPEN.total_seconds() // 3600)
    meaning = (
        '<details class="more"><summary>What stuck, spamming and flapping mean</summary>'
        '<ul class="plain">'
        f"<li><b>Stuck:</b> a Grafana alert firing {stuck_hours} hours or more without "
        "clearing.</li>"
        f"<li><b>Spamming:</b> an API alert sending {R6_API_SPAM_PER_24H} or more events a "
        "day.</li>"
        f"<li><b>Flapping:</b> fired and cleared {R6_FLAP_CYCLES} or more times in a day.</li>"
        "</ul></details>"
    )
    status = "" if any_pattern else '<span class="sub">No stuck, spamming or flapping alerts</span>'
    body = (
        '<section class="card">'
        f'<div class="card-h"><h2>Loudest alerts</h2>{status}</div>'
        f'<div class="table-wrap"><table class="list"><thead>{head}</thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>{meaning}</section>"
    )
    return _page(reviews, selected, "volume", body)


# ------------------------------------------------------------------ dashboards


def _listed(selected: Review, alerts: Sequence[SummaryAlert]) -> str:
    ordered = sorted(alerts, key=lambda a: (-a.row_count, a.schema, a.application, a.key_field))
    rows = "".join(
        "<tr>"
        f"{_alert_cell(_alert_href(selected, a.schema, a.application, a.key_field), a.message, a.application, a.component)}"
        f'<td>{_schema_chip(a.schema)}</td><td class="num r">{a.row_count:,}</td></tr>'
        for a in ordered[:TOP_LISTED]
    )
    return (
        '<div class="table-wrap"><table class="list"><thead><tr><th scope="col">Alert</th>'
        '<th scope="col">Schema</th><th scope="col" class="r">Events</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def dashboards_page(reviews: Sequence[Review], selected: Review, summary: TeamSummary) -> str:
    schemas = summary.inputs.schemas
    hidden = [a for a in summary.inputs.alerts if "R5" in a.core_rule_ids]
    r5 = _by_rule(summary.inputs.rules).get("R5", {})

    if any(schemas[s].suppressed for s in SCHEMAS):
        stats = "".join(
            f'<div class="stat"><span class="sub">{SCHEMA_NAMES[s]}</span>'
            + (
                f'<span class="big">{schemas[s].suppressed:,}</span><span class="sub">events · '
                f"{plural(r5[s].alerts if s in r5 else 0, 'alert')}</span>"
                if schemas[s].suppressed
                else '<span class="sub">None found</span>'
            )
            + "</div>"
            for s in SCHEMAS
        )
        more = (
            f'<p class="sub pad-x"><a href="{h(tab_url(selected.team_id, selected.week, "fix", rule="R5"))}">'
            "All hidden alerts in the fix list</a></p>"
            if len(hidden) > TOP_LISTED
            else ""
        )
        hidden_body = (
            f'<div class="stats">{stats}</div>{_listed(selected, hidden)}{more}'
            f'<p class="fixline pad-x"><b>Fix:</b> {h(fix("R5"))}</p>'
        )
    else:
        # Clauses the parser could not check are invisible here, so claim only what was checked.
        hidden_body = _quiet("None found in the filters we could check.")

    measured = [s for s in SCHEMAS if schemas[s].unseen is not None]
    if not measured:
        unseen_body = _quiet("Not measured this week.")
    else:
        lines = "".join(
            f"<dt>{SCHEMA_NAMES[s]}</dt><dd>"
            + (
                "Not measured this week"
                if schemas[s].unseen is None
                else (
                    f"{plural(schemas[s].unseen or 0, 'event')} · "
                    f"{plural(schemas[s].unseen_alerts or 0, 'alert')}"
                    if schemas[s].unseen
                    else "None found"
                )
            )
            + "</dd>"
            for s in SCHEMAS
        )
        unseen = [a for a in summary.inputs.alerts if a.unseen]
        unseen_body = f'<dl class="tot pad-x">{lines}</dl>' + (
            _listed(selected, unseen) if unseen else ""
        )

    body = (
        '<section class="card"><div class="card-h"><h2>Hidden by your panels</h2></div>'
        f"{hidden_body}</section>"
        '<section class="card"><div class="card-h"><h2>On no dashboard</h2></div>'
        f"{unseen_body}</section>"
    )
    return _page(reviews, selected, "dashboards", body)


# ------------------------------------------------------------------ migration


def migration_page(reviews: Sequence[Review], selected: Review, summary: TeamSummary) -> str:
    phases = "".join(
        f"<li{' class="on"' if key == selected.phase else ''}><b>{h(PHASES[key][0])}</b>"
        f'<span class="sub">{h(PHASES[key][1])}</span></li>'
        for key, _ in PHASE_STEPS
    )
    note = '<p class="sub">No alerts this week.</p>' if selected.phase == "no_data" else ""

    v1 = [a for a in summary.inputs.alerts if a.schema == "v1"]
    v2 = [a for a in summary.inputs.alerts if a.schema == "v2"]
    if v1:
        left = (
            f'<span class="big">{summary.estimate.rules_left:,}</span>'
            '<span class="sub">alert rules</span><dl class="tot">'
            f"<dt>Applications</dt><dd>{len({a.application for a in v1}):,}</dd>"
            f"<dt>Alerts</dt><dd>{len(v1):,}</dd></dl>"
        )
    else:
        left = '<p class="sub">No v1 alert fired this week.</p>'

    critical = [a for a in v2 if _critical(a.severity) and "R9" in a.readiness_rule_ids]
    if v2:
        gaps = "".join(
            f"<dt>{h(problem(rid))}</dt>"
            f"<dd>{sum(1 for a in v2 if rid in a.readiness_rule_ids):,}</dd>"
            for rid in ("R8", "R9", "R10")
        )
        ready = (
            f'<span class="big">{sum(_is_ready(a) for a in v2):,} of {len(v2):,}</span>'
            f'<span class="sub">alerts</span><dl class="tot">{gaps}</dl>'
        )
    else:
        ready = '<p class="sub">No v2 alerts this week.</p>'

    blocking = ""
    if critical:
        rows = "".join(
            "<tr>"
            f"{_alert_cell(_alert_href(selected, a.schema, a.application, a.key_field), a.message, a.application, a.component)}"
            f'<td class="num r">{a.row_count:,}</td></tr>'
            for a in sorted(critical, key=lambda a: (-a.row_count, a.application, a.key_field))
        )
        blocking = (
            '<section class="card"><div class="card-h"><h2>Critical, no runbook</h2>'
            '<span class="sub">Blocks phase 2</span></div><div class="table-wrap">'
            '<table class="list"><thead><tr><th scope="col">Alert</th>'
            f'<th scope="col" class="r">Events</th></tr></thead><tbody>{rows}</tbody></table>'
            "</div></section>"
        )

    body = (
        f'<ol class="phasecards">{phases}</ol>{note}'
        '<div class="two">'
        '<section class="card pad stack tight"><div class="eyebrow">Left to move</div>'
        f"<h2>{SCHEMA_NAMES['v1']}</h2>{left}</section>"
        '<section class="card pad stack tight"><div class="eyebrow">Ready for phase 2</div>'
        f"<h2>{SCHEMA_NAMES['v2']}</h2>{ready}</section>"
        f"</div>{blocking}"
    )
    return _page(reviews, selected, "migration", body)


# ------------------------------------------------------------------ history


def history_page(reviews: Sequence[Review], selected: Review) -> str:
    def points(schema: str, metric: str) -> list[ChartPoint]:
        return [
            ChartPoint(
                label=f"{review.window_end.day} {review.window_end:%b}",
                value=int(getattr(review.totals.get(schema), metric, 0) or 0),
                window_start=review.window_start,
                window_end=review.window_end,
                href=tab_url(review.team_id, review.week, "history"),
                title=f"Week of {format_week(review.window_start, review.window_end)}",
                selected=review.run_id == selected.run_id,
            )
            for review in reviews
        ]

    charts = "".join(
        f'<article class="card pad stack {schema}"><h2>{SCHEMA_NAMES[schema]}</h2>'
        '<div><div class="eyebrow">Alerts per week</div>'
        f"{line_chart(points(schema, 'distinct_alerts'), series=schema, label=f'{schema} alerts per week')}</div>"
        '<div><div class="eyebrow">Events per week</div>'
        f"{line_chart(points(schema, 'events'), series=schema, label=f'{schema} events per week')}</div>"
        "</article>"
        for schema in SCHEMAS
    )

    def number(review: Review, schema: str, metric: str) -> str:
        totals = review.totals.get(schema)
        return f'<td class="num r">{int(getattr(totals, metric, 0) or 0):,}</td>'

    rows = "".join(
        f"<tr{' class="current"' if review.run_id == selected.run_id else ''}>"
        f"<td>{h(format_week(review.window_start, review.window_end))}</td>"
        f"<td>{h(phase_label(review.phase))}</td>"
        f"{number(review, 'v1', 'distinct_alerts')}{number(review, 'v1', 'events')}"
        f"{number(review, 'v2', 'distinct_alerts')}{number(review, 'v2', 'events')}"
        f'<td class="num r">{"—" if review.readiness_pct is None else f"{round(review.readiness_pct)}%"}</td>'
        + (
            '<td class="sub">Viewing</td>'
            if review.run_id == selected.run_id
            else f'<td><a href="{h(tab_url(review.team_id, review.week, "overview"))}">Open</a></td>'
        )
        + "</tr>"
        for review in reversed(reviews)
    )
    table = (
        '<section class="card"><div class="card-h"><h2>Published weeks</h2></div>'
        '<div class="table-wrap"><table class="list"><thead><tr><th scope="col">Week</th>'
        '<th scope="col">Phase</th>'
        f'<th scope="col" class="r">{_schema_chip("v1")} alerts</th>'
        f'<th scope="col" class="r">{_schema_chip("v1")} events</th>'
        f'<th scope="col" class="r">{_schema_chip("v2")} alerts</th>'
        f'<th scope="col" class="r">{_schema_chip("v2")} events</th>'
        '<th scope="col" class="r">v2 ready</th><th scope="col"></th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></section>"
    )
    body = f'<div class="two">{charts}</div>{table}'
    return _page(reviews, selected, "history", body)


# ------------------------------------------------------------------ slides


def slides_page(reviews: Sequence[Review], selected: Review, summary: TeamSummary) -> str:
    return _page(reviews, selected, "slides", render_slides(summary))


# ------------------------------------------------------------------ one alert


def _field(label: str, value: Any, *, missing: str = "—", missing_class: str = "na") -> str:
    if value is None or value == "":
        return f'<dt>{h(label)}</dt><dd class="{missing_class}">{h(missing)}</dd>'
    return f"<dt>{h(label)}</dt><dd>{h(value)}</dd>"


def _latest_value(rule_id: str, alert: AlertRow) -> str | None:
    """What the latest event shows for the field a rule looks at, when that is meaningful."""
    if rule_id in ("R1", "R2"):
        return alert.message or ""
    if rule_id == "R3":
        return (
            f"application: {alert.application} · component: {alert.component or '(empty)'} · "
            f"node: {alert.node_name or '(none)'}"
        )
    if rule_id == "R4":
        return f"alert_rule_url: {alert.alert_rule_url or 'empty'}"
    if rule_id == "R7":
        return f"time_created {alert.time_created or '(none)'}"
    return None


def _history(decisions: Sequence[Decision], schema: str) -> str:
    note = (
        '<p class="sub">Adding an impact or a runbook gives the alert a new key, which starts '
        "without one.</p>"
        if schema == "v2"
        else ""
    )
    if not decisions:
        return f'<div class="history"><p class="sub">No decision yet.</p>{note}</div>'
    items = "".join(
        "<li>"
        f'<span class="chip human {h(d.state)}">{h(d.state.capitalize())}</span>'
        f'<span class="text">{h(d.note)}</span>'
        f'<span class="when">{h(format_instant(d.decided_at))} · {h(d.decided_by)}</span>'
        "</li>"
        for d in decisions
    )
    return f'<div class="history"><div class="eyebrow">Decisions</div><ol>{items}</ol>{note}</div>'


def _rule_card(alert: AlertRow, rule_id: str, decisions: Sequence[Decision]) -> str:
    sample = _sample(alert, rule_id)
    pattern = _pattern(alert) if rule_id == "R6" else None
    explanation = rule_explanation(rule_id, sample)
    readiness = rule_id in V2_READINESS_RULE_IDS
    title = problem(rule_id, pattern, critical=_critical(alert.severity))
    matched = (alert.evidence.get(rule_id) or {}).get("matched_rows")
    matched_text = (
        f"{int(matched):,} of {alert.row_count:,} events matched."
        if isinstance(matched, int)
        else ""
    )
    if readiness:
        evidence = (
            '<div class="ev one"><div><div class="lbl">Latest event</div>'
            f'<div class="val">{h(explanation.observed)}</div></div></div>'
        )
        chip = _chip("ready", "Get v2 ready")
    else:
        latest = _latest_value(rule_id, alert)
        matched_box = (
            '<div><div class="lbl">Matched event · stored sample</div>'
            f'<div class="val">{h(explanation.observed)}</div>'
            '<div class="hint">Can be older than the latest event.</div></div>'
        )
        evidence = (
            f'<div class="ev">{matched_box}<div><div class="lbl">Latest event</div>'
            f'<div class="val">{h(latest)}</div><div class="hint">{h(matched_text)}</div></div></div>'
            if latest is not None
            else f'<div class="ev one">{matched_box}</div>'
            + (f'<p class="sub">{h(matched_text)}</p>' if matched_text else "")
        )
        chip = _chip("rule", STATE_LABELS["rule_flagged"])
    return (
        f'<article class="card pad stack fcard{" ready" if readiness else ""}">'
        f'<h3>{chip}{h(title)}</h3><p class="sub">{h(why(rule_id, pattern, critical=_critical(alert.severity)))}</p>{evidence}'
        f'<p class="fixline"><b>Fix:</b> {h(fix(rule_id, pattern))}</p>'
        f"{_history(decisions, alert.alert_schema)}</article>"
    )


def _model_block(alert: AlertRow, decisions: Mapping[str, Sequence[Decision]]) -> str:
    if alert.quality_state == "rule_flagged":
        body = '<p class="sub">Skipped: the problems above already say what to fix.</p>'
        return f'<section class="card pad stack tight"><h2>Automated review</h2>{body}</section>'
    if alert.quality_state == "unassessed":
        return (
            '<section class="card pad stack tight"><h2>Automated review</h2>'
            '<p class="sub">Not reviewed this week.</p></section>'
        )
    principle = alert.model_finding
    if principle is None:
        return (
            '<section class="card pad stack tight"><h2>Automated review</h2>'
            '<p class="sub">No issue found.</p></section>'
        )
    review = alert.quality_state == "needs_review"
    title = principle_title(principle)
    chip = _chip("review" if review else "model", STATE_LABELS[alert.quality_state])
    action_line = (
        f'<p class="decide"><b>Decide:</b> {h(decision_question(principle))}</p>'
        if review
        else f'<p class="fixline"><b>Fix:</b> {h(principle_next_step(principle))}</p>'
    )
    return (
        '<article class="card pad stack fcard model">'
        f"<h3>{chip}{'Possibly ' if review else ''}{h(title)}</h3>"
        f'<p class="sub">{h(alert.llm_confidence or "unknown")} confidence · advisory, not '
        "counted in the totals</p>"
        f"<blockquote>{h(alert.llm_justification or '')}</blockquote>"
        f"{action_line}{_history(decisions.get(principle, []), alert.alert_schema)}</article>"
    )


def alert_page(reviews: Sequence[Review], selected: Review, detail: AlertDetail) -> str:
    alert = detail.alert
    v2 = alert.alert_schema == "v2"
    grafana = (alert.provider or "").lower() == "grafana"
    if alert.alert_rule_url:
        rule_link = f"<dt>Rule link</dt><dd>{link(alert.alert_rule_url)}</dd>"
    else:
        rule_link = _field(
            "Rule link",
            None,
            missing="missing" if grafana else "none (API alert)",
            missing_class="missing" if grafana else "na",
        )
    fields = [
        _field("Application", alert.application),
        _field("Component", alert.component),
        _field("Severity", alert.severity),
    ]
    if v2:
        fields += [_field("Environment", alert.environment), _field("Status", alert.alert_status)]
    fields += [_field("Provider", alert.provider), _field("Node", alert.node_name), rule_link]
    if v2:
        fields.append(_field("Impact", alert.impact, missing="missing", missing_class="missing"))
        fields.append(
            f"<dt>Runbook</dt><dd>{link(alert.runbook_url)}</dd>"
            if alert.runbook_url
            else _field("Runbook", None, missing="missing", missing_class="missing")
        )
    fields += [
        _field("First seen", format_instant(alert.first_seen)),
        _field("Last seen", format_instant(alert.last_seen)),
    ]

    core = "".join(
        _rule_card(alert, rid, detail.decisions.get(rid, [])) for rid in alert.core_rule_ids
    )
    quality = (
        f'<section class="stack"><h2>What to fix</h2>{core}</section>'
        if core
        else _quiet("No problem found by the standard rules.")
    )
    readiness = ""
    if v2:
        cards = "".join(
            _rule_card(alert, rid, detail.decisions.get(rid, []))
            for rid in alert.readiness_rule_ids
        )
        readiness = (
            '<section class="stack"><h2>Get v2 ready</h2>'
            + (cards or _quiet("Impact and runbook are both set."))
            + "</section>"
        )
    chips = _problems(alert)
    back = tab_url(selected.team_id, selected.week, "fix") + "#alerts"
    message = alert.message or "(no message)"
    body = (
        f'<p class="back"><a href="{h(back)}">← Fix list</a></p>'
        '<section class="stack tight">'
        f'<div class="src">{h(alert.application)} &rsaquo; {h(alert.component or "—")} '
        f"{_schema_chip(alert.alert_schema)}</div>"
        f'<h2 class="alert-title">“{h(message)}”</h2>'
        f'<div class="why">{"".join(chips)}<span class="sub">{alert.row_count:,} events</span>'
        "</div></section>"
        '<div class="split"><div class="colmain">'
        f"{quality}{_model_block(alert, detail.decisions)}{readiness}</div>"
        '<aside class="colside"><section class="card pad stack">'
        '<div class="eyebrow">Latest event</div>'
        f'<dl class="doc">{"".join(fields)}</dl></section>'
        '<details class="card pad tech"><summary>Technical details</summary>'
        f'<dl class="doc"><dt>Key</dt><dd class="mono">{h(alert.key_field)}</dd></dl>'
        "</details></aside></div>"
    )
    return _page(reviews, selected, "fix", body, crumb=message)
