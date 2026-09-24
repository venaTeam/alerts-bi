"""The portal's HTML, rendered on the server from what :mod:`src.portal.queries` returns.

Alert values are free text written by other teams and by a model. Every one goes through
:func:`h`, and a URL becomes a link only when :func:`safe_link` accepts it as an absolute
``http(s)`` address with a host - a ``javascript:`` runbook is shown as text, never followed.

The pages carry no script. The work-list filters, pagination, the week picker and the chart
points are plain links and a GET form, so the portal works with scripting disabled and its
Content-Security-Policy can forbid scripts outright.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from html import escape
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from src.portal.assets import STYLESHEET_PATH
from src.portal.charts import ChartPoint, line_chart
from src.portal.explain import (
    QUALITY_STATE_LABELS,
    decision_question,
    format_date,
    format_instant,
    format_week,
    principle_next_step,
    principle_title,
    rule_explanation,
)
from src.portal.queries import (
    AlertDetail,
    AlertRow,
    Decision,
    Review,
    SchemaTotals,
    TeamSummary,
    WorklistPage,
)

__all__ = [
    "alert_page",
    "alert_url",
    "directory_page",
    "error_page",
    "h",
    "safe_link",
    "team_page",
    "team_url",
    "week_url",
]

SCHEMA_NAMES = {"v1": "Appchi", "v2": "Appchi V2"}
REPEATS = {"v1": "repeats every 5 minutes", "v2": "repeats every 12 hours"}
PHASE_STEPS = (
    ("phase_0", "Phase 0 · Clean up"),
    ("phase_1", "Phase 1 · New rules"),
    ("phase_2", "Phase 2 · Enrich"),
    ("done", "Done"),
)


def h(value: Any) -> str:
    """Escape any value for HTML text and attribute contexts."""
    return "" if value is None else escape(str(value), quote=True)


def safe_link(url: Any) -> str | None:
    """Return ``url`` when it is an absolute http(s) URL with a host, else ``None``."""
    if not isinstance(url, str) or not url.strip():
        return None
    candidate = url.strip()
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    if any(ord(ch) < 32 or ch.isspace() for ch in candidate):
        return None
    return candidate


def _link(url: Any) -> str:
    target = safe_link(url)
    if target is None:
        return f'<span class="missing">{h(url)} · not a usable http(s) link</span>'
    return (
        f'<a href="{h(target)}" rel="noopener noreferrer nofollow" target="_blank">{h(target)}</a>'
    )


# ------------------------------------------------------------------ URLs


def team_url(team_id: str) -> str:
    return "/teams/" + quote(team_id, safe="")


def week_url(team_id: str, week: date, **query: Any) -> str:
    base = f"{team_url(team_id)}/weeks/{week.isoformat()}"
    params = {key: value for key, value in query.items() if value not in (None, "")}
    return base + ("?" + urlencode(params) if params else "")


def alert_url(team_id: str, week: date, alert: AlertRow) -> str:
    return f"{team_url(team_id)}/weeks/{week.isoformat()}/alert?" + urlencode(
        {"schema": alert.alert_schema, "application": alert.application, "key": alert.key_field}
    )


# ------------------------------------------------------------------ shell


def _layout(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>{h(title)} · Alerts BI</title>"
        f'<link rel="stylesheet" href="{STYLESHEET_PATH}">'
        "</head><body>"
        '<header class="topbar"><div class="wrap">'
        '<a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span>'
        "Alerts BI <small>· Review portal</small></a>"
        '<span class="ro" title="This portal is view-only.">Read-only</span>'
        "</div></header>"
        f'<main class="wrap">{body}</main>'
        "</body></html>\n"
    )


def error_page(status: int, message: str) -> str:
    return _layout(
        f"{status}",
        f'<section class="intro"><div class="eyebrow">{status}</div><h1>{h(message)}</h1>'
        '<p><a href="/">Back to all teams</a></p></section>',
    )


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


# ------------------------------------------------------------------ directory


def directory_page(teams: Sequence[TeamSummary]) -> str:
    if not teams:
        return _layout(
            "Team alert reviews",
            '<section class="intro"><div class="eyebrow">Published reviews</div>'
            "<h1>Team alert reviews</h1><p>No weekly review has been published yet.</p></section>",
        )

    def volume(review: Review, schema: str) -> str:
        totals = review.totals.get(schema, SchemaTotals())
        if totals.events == 0 and totals.distinct_alerts == 0:
            return f'<span class="sub">no {schema} alerts</span>'
        return (
            f'<span class="vol"><b>{_plural(totals.distinct_alerts, "distinct alert")}</b>'
            f'<span class="sub">{totals.events:,} events</span></span>'
        )

    rows = []
    for summary in teams:
        review = summary.latest
        attention = review.needs_attention
        rows.append(
            "<tr>"
            f'<td><a class="team" href="{h(team_url(review.team_id))}">{h(review.team_name)}</a></td>'
            f"<td>{h(_phase_label(review.phase))}"
            f'<div class="sub">Phase-2 readiness {h(_readiness(review.readiness_pct))}</div></td>'
            f"<td>{volume(review, 'v1')}</td><td>{volume(review, 'v2')}</td>"
            f'<td class="num">{_plural(attention, "alert") if attention else "none"}</td>'
            f"<td>{h(format_week(review.window_start, review.window_end))}"
            f'<div class="sub">published {h(format_date(review.published_at))}</div></td>'
            f'<td class="num">{summary.weeks}</td>'
            "</tr>"
        )
    body = (
        '<section class="intro"><div class="eyebrow">Published reviews</div>'
        "<h1>Team alert reviews</h1>"
        "<p>Each team's most recent weekly review. Open a team to see its alerts, what needs "
        "fixing, and how its numbers have moved week to week. Teams are listed alphabetically; "
        "this is not a ranking.</p></section>"
        '<section class="card table-wrap"><table class="dir"><thead><tr>'
        '<th scope="col">Team</th><th scope="col">Migration phase</th>'
        '<th scope="col" class="v1">v1 (Appchi)</th><th scope="col" class="v2">v2 (Appchi V2)</th>'
        '<th scope="col">Needs attention</th><th scope="col">Latest week</th>'
        '<th scope="col">Weeks reviewed</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
    )
    return _layout("Team alert reviews", body)


def _phase_label(phase: str) -> str:
    for key, label in PHASE_STEPS:
        if key == phase:
            return label
    return "No alerts this week" if phase == "no_data" else phase


def _readiness(value: float | None) -> str:
    return "—" if value is None else f"{round(value)}%"


# ------------------------------------------------------------------ team page


_STATES = (
    ("rule_flagged", "q-rule"),
    ("llm_flagged", "q-model"),
    ("needs_review", "q-review"),
    ("assessed_good", "q-good"),
    ("unassessed", "q-un"),
)


def _quality(totals: SchemaTotals) -> str:
    counts = [(state, css, int(getattr(totals, state))) for state, css in _STATES]
    total = sum(count for _, _, count in counts) or 1
    rects, offset = [], 0.0
    for state, css, count in counts:
        if not count:
            continue
        width = 100 * count / total
        rects.append(
            f'<rect class="{css}" x="{offset:.3f}" y="0" width="{max(width - 0.4, 0.2):.3f}" '
            f'height="10"><title>{h(QUALITY_STATE_LABELS[state])}: {count}</title></rect>'
        )
        offset += width
    legend = "".join(
        f'<li><svg viewBox="0 0 9 9" aria-hidden="true"><rect class="{css}" width="9" height="9" '
        f'rx="2"/></svg>{h(QUALITY_STATE_LABELS[state])} <b class="num">{count}</b></li>'
        for state, css, count in counts
        if count
    )
    return (
        f'<svg class="qbar" viewBox="0 0 100 10" preserveAspectRatio="none" role="img" '
        f'aria-label="Alert quality">{"".join(rects)}</svg><ul class="legend">{legend}</ul>'
    )


def _schema_card(review: Review, schema: str) -> str:
    totals = review.totals.get(schema, SchemaTotals())
    heading = (
        f'<h3><span class="chip {schema}">{schema}</span>{SCHEMA_NAMES[schema]} '
        f"<small>{REPEATS[schema]}</small></h3>"
    )
    if totals.events == 0 and totals.distinct_alerts == 0:
        return (
            f'<article class="card schema {schema}">{heading}'
            f'<p class="sub">No {schema} alerts fired this week.</p></article>'
        )
    facts = [
        "<dt>Events with a rule finding</dt>"
        f'<dd class="num">{totals.flagged_rows:,} of {totals.events:,}</dd>'
    ]
    if totals.suppressed:
        facts.append(
            f'<dt>Hidden by your own dashboard</dt><dd class="num">{totals.suppressed:,} events</dd>'
        )
    if schema == "v2":
        facts.append(
            "<dt>v2 readiness gaps</dt>"
            f"<dd>{_plural(totals.readiness_gaps, 'alert')} missing an impact or a usable "
            "runbook. Counted apart from quality.</dd>"
        )
    return (
        f'<article class="card schema {schema}">{heading}'
        '<div class="kpis">'
        f'<div class="kpi"><span class="n">{totals.distinct_alerts:,}</span>'
        '<span class="u">distinct alerts this week</span>'
        '<span class="d">each alert counted once, however often it fired</span></div>'
        f'<div class="kpi"><span class="n">{totals.events:,}</span>'
        '<span class="u">alert events this week</span>'
        '<span class="d">every firing, repeats included</span></div>'
        "</div>"
        f'<div><div class="eyebrow">What we found in these '
        f"{_plural(totals.distinct_alerts, 'alert')}</div>{_quality(totals)}</div>"
        f'<dl class="facts">{"".join(facts)}</dl>'
        "</article>"
    )


def _history(reviews: Sequence[Review], selected: Review, schema: str) -> str:
    def points(metric: str) -> list[ChartPoint]:
        return [
            ChartPoint(
                label=f"{review.window_end.day} {review.window_end:%b}",
                value=int(getattr(review.totals.get(schema, SchemaTotals()), metric)),
                window_start=review.window_start,
                window_end=review.window_end,
                href=week_url(review.team_id, review.week),
                title=f"Week of {format_week(review.window_start, review.window_end)}",
                selected=review.run_id == selected.run_id,
            )
            for review in reviews
        ]

    return (
        f'<article class="card hist {schema}">'
        f'<h3><span class="chip {schema}">{schema}</span> {SCHEMA_NAMES[schema]}</h3>'
        '<div><div class="chart-title">Distinct alerts per week<span>each alert counted once</span></div>'
        f"{line_chart(points('distinct_alerts'), series=schema, label=f'{schema} distinct alerts per week')}</div>"
        '<div><div class="chart-title">Alert events per week<span>every firing, repeats included</span></div>'
        f"{line_chart(points('events'), series=schema, label=f'{schema} alert events per week')}</div>"
        "</article>"
    )


def _kind(alert: AlertRow) -> str:
    return {0: "", 1: "model", 2: "review", 3: "ready"}.get(alert.attention_rank, "good")


def _reasons(alert: AlertRow, decided: Mapping[str, Decision]) -> str:
    chips = [
        f'<span class="chip rule">{h(rule_explanation(rule, _sample(alert, rule)).reason)}</span>'
        for rule in alert.core_rule_ids
    ]
    principle = alert.model_finding
    if principle and alert.quality_state == "llm_flagged":
        chips.append(
            f'<span class="chip model">Automated: {h(principle_title(principle))} · advisory</span>'
        )
    elif principle:
        chips.append(
            f'<span class="chip model">Needs a decision: possibly {h(principle_title(principle))}</span>'
        )
    chips.extend(
        f'<span class="chip ready">{h(rule_explanation(rule, _sample(alert, rule)).reason)}</span>'
        for rule in alert.readiness_rule_ids
    )
    if not chips:
        label = QUALITY_STATE_LABELS.get(alert.quality_state, alert.quality_state)
        chips.append(f'<span class="chip good">{h(label)}</span>')
    for decision in decided.values():
        chips.append(
            f'<span class="chip human {h(decision.state)}" title="Human decision">'
            f"{h(decision.state.capitalize())}</span>"
        )
    return "".join(chips)


def _sample(alert: AlertRow, rule_id: str) -> dict[str, Any]:
    entry = alert.evidence.get(rule_id) or {}
    sample = entry.get("sample_evidence")
    return sample if isinstance(sample, dict) else {}


def _row(team_id: str, week: date, alert: AlertRow, decided: Mapping[str, Decision]) -> str:
    context = [
        f'<span class="src">{h(alert.application)} &rsaquo; {h(alert.component or "—")}</span>',
        f'<span class="chip {alert.alert_schema}">{alert.alert_schema}</span>',
    ]
    context.extend(
        f"<span>{h(value)}</span>"
        for value in (alert.severity, alert.environment, alert.provider)
        if value
    )
    return (
        f'<li><a class="row" href="{h(alert_url(team_id, week, alert))}">'
        f'<span class="stripe {_kind(alert)}"></span>'
        '<span class="body">'
        f'<span class="msg">“{h(alert.message or "(no message)")}”</span>'
        f'<span class="ctx">{"".join(context)}</span>'
        f'<span class="why">{_reasons(alert, decided)}</span>'
        "</span>"
        f'<span class="side"><b>{alert.row_count:,}</b>firings'
        f"<span>last {h(format_instant(alert.last_seen))}</span></span>"
        "</a></li>"
    )


def _segment(options: Iterable[tuple[str, str, bool]]) -> str:
    return "".join(
        f'<a href="{h(href)}"{' aria-current="true"' if current else ""}>{h(text)}</a>'
        for text, href, current in options
    )


def team_page(
    reviews: Sequence[Review],
    selected: Review,
    page: WorklistPage,
    decided: Mapping[tuple[str, str, str], Mapping[str, Decision]],
    *,
    show: str,
    schema: str,
    counts: Mapping[str, int],
) -> str:
    team_id = selected.team_id
    week = selected.week

    options = "".join(
        f'<option value="{review.week.isoformat()}"'
        f"{' selected' if review.run_id == selected.run_id else ''}>"
        f"Week of {h(format_week(review.window_start, review.window_end))}</option>"
        for review in reversed(reviews)
    )
    steps = "".join(
        f'<span class="{"on" if key == selected.phase else ""}">{h(label)}</span>'
        for key, label in PHASE_STEPS
    )
    if selected.readiness_pct is None:
        meter = '<span class="sub">— no v2 alerts this week</span>'
    else:
        width = max(0.0, min(100.0, selected.readiness_pct))
        meter = (
            '<svg viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">'
            '<rect class="track" x="0" y="0" width="100" height="8" rx="2"/>'
            f'<rect class="fill" x="0" y="0" width="{width:.2f}" height="8" rx="2"/></svg>'
            f"<b>{round(width)}%</b>"
            '<span class="sub">of v2 alerts have an impact, plus a runbook where critical</span>'
        )

    def filter_link(**changes: Any) -> str:
        state = {"show": show, "schema": schema, **changes}
        return (
            week_url(
                team_id,
                week,
                show=None if state["show"] == "attention" else state["show"],
                schema=None if state["schema"] == "all" else state["schema"],
            )
            + "#worklist"
        )

    tools = (
        '<div class="tools">'
        '<span class="seg" role="group" aria-label="Which alerts">'
        + _segment(
            [
                (
                    f"Needs attention · {counts['attention']}",
                    filter_link(show="attention"),
                    show == "attention",
                ),
                (f"All alerts · {counts['all']}", filter_link(show="all"), show == "all"),
            ]
        )
        + '</span><span class="seg" role="group" aria-label="Schema">'
        + _segment(
            [
                ("v1 + v2", filter_link(schema="all"), schema == "all"),
                ("v1", filter_link(schema="v1"), schema == "v1"),
                ("v2", filter_link(schema="v2"), schema == "v2"),
            ]
        )
        + "</span></div>"
    )

    if page.rows:
        items = "".join(
            _row(
                team_id,
                week,
                alert,
                decided.get((alert.alert_schema, alert.application, alert.key_field), {}),
            )
            for alert in page.rows
        )
        listing = f'<ul class="wl">{items}</ul>'
    else:
        listing = '<p class="empty">Nothing matches this filter.</p>'

    first = (page.page - 1) * page.page_size + 1 if page.total else 0
    last = min(page.page * page.page_size, page.total)

    def page_link(target: int, text: str, enabled: bool) -> str:
        if not enabled:
            return f'<span class="button" aria-disabled="true">{text}</span>'
        href = week_url(
            team_id,
            week,
            show=None if show == "attention" else show,
            schema=None if schema == "all" else schema,
            page=target,
        )
        return f'<a class="button" href="{h(href)}#worklist">{text}</a>'

    pager = (
        f'<div class="pager"><span>Showing {first}&ndash;{last} of {page.total:,} · one row per '
        "distinct alert</span>"
        f'<span class="links">{page_link(page.page - 1, "Previous", page.page > 1)}'
        f"{page_link(page.page + 1, 'Next', page.page < page.pages)}</span></div>"
    )

    note = (
        f'<section class="note"><div class="eyebrow">Review note · standardization team</div>'
        f"<p>{h(selected.review_note)}</p></section>"
        if selected.review_note
        else ""
    )

    body = (
        f'<nav class="crumbs"><a href="/">All teams</a><span>/</span><span>{h(selected.team_name)}</span></nav>'
        '<section class="head">'
        f'<div><div class="eyebrow">Weekly alert review</div><h1>{h(selected.team_name)}</h1></div>'
        f'<form class="weeks" method="get" action="{h(team_url(team_id))}/weeks">'
        f'<label class="sub" for="week">Week</label><select id="week" name="week">{options}</select>'
        '<button class="button" type="submit">Open</button></form>'
        "</section>"
        '<section class="meta">'
        f'<span><span class="k">Week covered</span>{h(format_week(selected.window_start, selected.window_end))}</span>'
        f'<span><span class="k">Published</span>{h(format_instant(selected.published_at))}</span>'
        "</section>"
        f"{note}"
        '<section><div class="section-h"><h2>This week</h2><p>v1 and v2 are counted separately: '
        "they repeat at different intervals and identify alerts differently.</p></div>"
        '<div class="card phase">'
        f'<div><div class="eyebrow">Migration phase</div><div class="steps">{steps}</div></div>'
        f'<div><div class="eyebrow">Phase-2 readiness</div><div class="meter">{meter}</div></div>'
        "</div>"
        f'<div class="two">{_schema_card(selected, "v1")}{_schema_card(selected, "v2")}</div>'
        "</section>"
        '<section><div class="section-h"><h2>Over time</h2><p>One point per published week, '
        "dated by the day the week ends. Select a point to open that week.</p></div>"
        f'<div class="two">{_history(reviews, selected, "v1")}{_history(reviews, selected, "v2")}</div>'
        "</section>"
        '<section class="card" id="worklist">'
        '<div class="section-h tools"><h2>Work list</h2><p>Alerts that need attention this week. '
        "Open one to see why and what to do next.</p></div>"
        f"{tools}{listing}{pager}"
        "</section>"
    )
    return _layout(
        f"{selected.team_name} · week of {format_week(selected.window_start, selected.window_end)}",
        body,
    )


# ------------------------------------------------------------------ alert page


def _field(label: str, value: Any, *, missing: str = "not set", missing_class: str = "na") -> str:
    if value is None or value == "":
        return f'<dt>{h(label)}</dt><dd class="{missing_class}">{h(missing)}</dd>'
    return f"<dt>{h(label)}</dt><dd>{h(value)}</dd>"


def _raw_field(label: str, html: str) -> str:
    return f"<dt>{h(label)}</dt><dd>{html}</dd>"


def _latest_value(rule_id: str, alert: AlertRow) -> str | None:
    """What the latest firing shows for the field a rule looks at, when that is meaningful."""
    if rule_id in ("R1", "R2"):
        return alert.message or ""
    if rule_id == "R3":
        return (
            f"application: {alert.application} · component: {alert.component or '(empty)'} · "
            f"node: {alert.node_name or '(none)'}"
        )
    if rule_id == "R4":
        return (
            f"provider: {alert.provider or ''} · alert_rule_url: {alert.alert_rule_url or 'empty'}"
        )
    if rule_id == "R7":
        return f"time_created {alert.time_created or '(none)'} · received {format_instant(alert.representative_at)}"
    return None


def _decision_history(decisions: Sequence[Decision], schema: str) -> str:
    key_note = (
        "A decision belongs to this exact alert key. If the team adds an impact or a runbook, "
        "the alert gets a new key and starts without one."
        if schema == "v2"
        else ""
    )
    if not decisions:
        return (
            '<div class="history"><span class="sub">No human decision recorded.'
            f"{' ' + key_note if key_note else ''}</span></div>"
        )
    items = "".join(
        "<li>"
        f'<span class="chip human {h(d.state)}">{h(d.state.capitalize())}</span>'
        f'<span class="text">{h(d.note)}</span>'
        f'<span class="when">{h(format_instant(d.decided_at))} · {h(d.decided_by)}</span>'
        "</li>"
        for d in decisions
    )
    return (
        '<div class="history"><div class="eyebrow">Human decisions · oldest first</div>'
        f"<ol>{items}</ol>"
        f"{'<span class="sub">' + key_note + '</span>' if key_note else ''}</div>"
    )


def _rule_card(alert: AlertRow, rule_id: str, decisions: Sequence[Decision]) -> str:
    entry = alert.evidence.get(rule_id) or {}
    explanation = rule_explanation(rule_id, _sample(alert, rule_id))
    matched = entry.get("matched_rows")
    matched_text = (
        f"{int(matched):,} of {alert.row_count:,} firings this week matched."
        if isinstance(matched, int)
        else ""
    )

    if explanation.readiness:
        evidence = (
            '<div class="ev one"><div><div class="lbl">Latest firing</div>'
            f'<div class="val">{h(explanation.observed)}</div>'
            '<div class="hint">Readiness is checked on the latest firing only.</div></div></div>'
        )
        chip = '<span class="chip ready">Readiness gap</span>'
        css = "finding ready"
    else:
        latest = _latest_value(rule_id, alert)
        sample_box = (
            '<div><div class="lbl">Matching firing · stored sample</div>'
            f'<div class="val">{h(explanation.observed)}</div>'
            '<div class="hint">One firing that matched this week. It can be older than the '
            "latest firing and differ from it.</div></div>"
        )
        if latest is None:
            evidence = f'<div class="ev one">{sample_box}</div>' + (
                f'<p class="sub">{h(matched_text)}</p>' if matched_text else ""
            )
        else:
            evidence = (
                f'<div class="ev">{sample_box}'
                f'<div><div class="lbl">Latest firing</div><div class="val">{h(latest)}</div>'
                f'<div class="hint">{h(matched_text)}</div></div></div>'
            )
        chip = '<span class="chip rule">Rule finding</span>'
        css = "finding"

    return (
        f'<article class="{css}">'
        f"<h3>{chip}{h(explanation.title)}</h3>"
        f"<p>{h(explanation.why)}</p>"
        f"{evidence}"
        f'<div class="next"><b>Next step:</b> {h(explanation.next_step)}</div>'
        f"{_decision_history(decisions, alert.alert_schema)}"
        "</article>"
    )


def _model_block(alert: AlertRow, decisions: Mapping[str, Sequence[Decision]]) -> str:
    if alert.quality_state == "rule_flagged":
        return '<p class="sub">Skipped: the rule findings above already say what to fix.</p>'
    if alert.quality_state == "unassessed":
        return '<p class="sub">Not reviewed this week.</p>'
    principle = alert.model_finding
    if principle is None:
        return "<p>No issue found.</p>"

    review = alert.quality_state == "needs_review"
    confidence = alert.llm_confidence or "unknown"
    stance = (
        "Not certain enough to count as a finding, so a person decides."
        if review
        else "Advisory: shown to help, and not counted in the rule-based totals."
    )
    action = (
        f'<div class="decide"><b>Decision needed:</b> {h(decision_question(principle))}</div>'
        if review
        else f'<div class="next"><b>Next step:</b> {h(principle_next_step(principle))}</div>'
    )
    return (
        '<article class="finding model">'
        f'<h3><span class="chip model">Automated finding · advisory</span>{h(principle_title(principle))}</h3>'
        f"<p>Confidence: <b>{h(confidence)}</b>. {stance}</p>"
        f"<blockquote>{h(alert.llm_justification or '')}</blockquote>"
        '<span class="sub">The automated review\'s original reasoning, unedited.</span>'
        f"{action}"
        f"{_decision_history(decisions.get(principle, []), alert.alert_schema)}"
        "</article>"
    )


def alert_page(review: Review, detail: AlertDetail) -> str:
    alert = detail.alert
    v2 = alert.alert_schema == "v2"
    not_in_v1 = {"missing": "not part of the v1 schema"}
    alert_rule = (
        _raw_field("Alert rule", _link(alert.alert_rule_url))
        if alert.alert_rule_url
        else _field(
            "Alert rule",
            None,
            missing="missing"
            if (alert.provider or "").lower() == "grafana"
            else "none (API alert)",
            missing_class="missing" if (alert.provider or "").lower() == "grafana" else "na",
        )
    )
    fields = [
        _field("Message", f"“{alert.message}”" if alert.message else None),
        _field("Application", alert.application),
        _field("Component", alert.component),
        _raw_field(
            "Schema",
            f'<span class="chip {alert.alert_schema}">{alert.alert_schema}</span> '
            f"{SCHEMA_NAMES[alert.alert_schema]}",
        ),
        _field("Severity", alert.severity),
        _field("Environment", alert.environment)
        if v2
        else _field("Environment", None, **not_in_v1),
    ]
    if v2:
        fields.append(_field("Status", alert.alert_status))
    fields.extend(
        [
            _field("Provider", alert.provider),
            _field("Node", alert.node_name, missing="not set (optional)"),
            _field("Impact", alert.impact, missing="missing", missing_class="missing")
            if v2
            else _field("Impact", None, **not_in_v1),
        ]
    )
    if v2:
        fields.append(
            _raw_field("Runbook", _link(alert.runbook_url))
            if alert.runbook_url
            else _field("Runbook", None, missing="missing", missing_class="missing")
        )
    else:
        fields.append(_field("Runbook", None, **not_in_v1))
    fields.extend(
        [
            alert_rule,
            _field("First seen", format_instant(alert.first_seen)),
            _field("Last seen", format_instant(alert.last_seen)),
            _field("Firings this week", f"{alert.row_count:,}"),
        ]
    )

    core = (
        "".join(
            _rule_card(alert, rule, detail.decisions.get(rule, [])) for rule in alert.core_rule_ids
        )
        or '<p class="sub">No rule matched any firing of this alert this week.</p>'
    )
    readiness = (
        "".join(
            _rule_card(alert, rule, detail.decisions.get(rule, []))
            for rule in alert.readiness_rule_ids
        )
        or '<p class="sub">An impact and a usable runbook are both present.</p>'
    )

    technical = [
        _raw_field("key_field", f'<span class="mono">{h(alert.key_field)}</span>'),
        _field("Rule IDs", ", ".join([*alert.core_rule_ids, *alert.readiness_rule_ids]) or "none"),
    ]
    if alert.model_finding:
        technical.append(_field("Principle ID", alert.model_finding))

    week_label = format_week(review.window_start, review.window_end)
    back = week_url(review.team_id, review.week) + "#worklist"
    body = (
        f'<nav class="crumbs"><a href="/">All teams</a><span>/</span>'
        f'<a href="{h(team_url(review.team_id))}">{h(review.team_name)}</a><span>/</span>'
        f'<a href="{h(back)}">Week of {h(week_label)}</a></nav>'
        f'<section><div class="eyebrow">{h(alert.application)} &rsaquo; {h(alert.component or "—")} · '
        f"{alert.alert_schema}</div><h1>“{h(alert.message or '(no message)')}”</h1></section>"
        '<section class="block"><h2>Latest firing <span class="eyebrow">as last received this week</span></h2>'
        f'<dl class="doc">{"".join(fields)}</dl></section>'
        f'<section class="block"><h2>Quality findings <span class="eyebrow">standard rules</span></h2>{core}</section>'
        f'<section class="block"><h2>Automated review <span class="eyebrow">advisory</span></h2>'
        f"{_model_block(alert, detail.decisions)}</section>"
        + (
            f'<section class="block"><h2>v2 readiness <span class="eyebrow">counted apart from quality</span></h2>{readiness}</section>'
            if v2
            else ""
        )
        + '<details class="block tech"><summary>Technical details</summary>'
        f'<dl class="doc">{"".join(technical)}</dl></details>'
        f'<p class="back"><a href="{h(back)}">Back to the work list</a></p>'
    )
    return _layout(f"{alert.message or alert.key_field} · {review.team_name}", body)
