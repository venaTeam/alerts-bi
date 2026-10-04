"""The portal's page shell, its directory and the helpers every page shares.

Alert values are free text written by other teams and by a model. Every one goes through
:func:`h`, and a URL becomes a link only when :func:`safe_link` accepts it as an absolute
``http(s)`` address with a host - a ``javascript:`` runbook is shown as text, never followed.

The pages carry no script. Tabs, filters, pagination, the week menu and the chart points are
plain links, so the portal works with scripting disabled and its Content-Security-Policy can
forbid scripts outright. A team's week is rendered by :mod:`src.portal.tabs`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from html import escape
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from src.portal.assets import STYLESHEET_PATH
from src.portal.explain import format_date, format_week
from src.portal.queries import Review, SchemaTotals, TeamSummary

__all__ = [
    "PHASE_STEPS",
    "SCHEMA_NAMES",
    "TABS",
    "alert_url",
    "directory_page",
    "error_page",
    "h",
    "layout",
    "link",
    "phase_label",
    "plural",
    "safe_link",
    "tab_url",
    "team_url",
    "week_url",
]

SCHEMA_NAMES = {"v1": "Appchi", "v2": "Appchi V2"}
PHASE_STEPS = (
    ("phase_0", "Phase 0 · Clean up"),
    ("phase_1", "Phase 1 · New rules"),
    ("phase_2", "Phase 2 · Enrich"),
    ("done", "Done"),
)
#: The team week's tabs: (key, label, path segment after the week). Overview is the week's
#: own address, so links published before the tabs existed still open the week.
TABS = (
    ("overview", "Overview", ""),
    ("fix", "Fix list", "fix"),
    ("volume", "Volume", "volume"),
    ("dashboards", "Dashboards", "dashboards"),
    ("migration", "Migration", "migration"),
    ("history", "History", "history"),
    ("slides", "Slides", "slides"),
)
_TAB_PATHS = {key: path for key, _, path in TABS}


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


def link(url: Any) -> str:
    """An external link, or the value as text with a note when it is not a usable link."""
    target = safe_link(url)
    if target is None:
        return f'<span class="missing">{h(url)} · not a usable http(s) link</span>'
    return (
        f'<a href="{h(target)}" rel="noopener noreferrer nofollow" target="_blank">{h(target)}</a>'
    )


def plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


# ------------------------------------------------------------------ URLs


def team_url(team_id: str) -> str:
    return "/teams/" + quote(team_id, safe="")


def week_url(team_id: str, week: date, **query: Any) -> str:
    return tab_url(team_id, week, "overview", **query)


def tab_url(team_id: str, week: date, tab: str, **query: Any) -> str:
    """One tab of a published week. Empty and ``None`` query values are left out."""
    base = f"{team_url(team_id)}/weeks/{week.isoformat()}"
    path = _TAB_PATHS[tab]
    params = {key: value for key, value in query.items() if value not in (None, "")}
    return base + (f"/{path}" if path else "") + ("?" + urlencode(params) if params else "")


def alert_url(team_id: str, week: date, schema: str, application: str, key: str) -> str:
    """One alert of a published week, opened from the Fix list."""
    return f"{team_url(team_id)}/weeks/{week.isoformat()}/alert?" + urlencode(
        {"schema": schema, "application": application, "key": key}
    )


# ------------------------------------------------------------------ shell


def layout(title: str, body: str) -> str:
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
    return layout(
        f"{status}",
        f'<section class="intro"><div class="eyebrow">{status}</div><h1>{h(message)}</h1>'
        '<p><a href="/">Back to all teams</a></p></section>',
    )


def phase_label(phase: str) -> str:
    for key, label in PHASE_STEPS:
        if key == phase:
            return label
    return "No alerts this week" if phase == "no_data" else phase


def _readiness(value: float | None) -> str:
    return "—" if value is None else f"{round(value)}%"


# ------------------------------------------------------------------ directory


def directory_page(teams: Sequence[TeamSummary]) -> str:
    if not teams:
        return layout(
            "Team alert reviews",
            '<section class="intro"><div class="eyebrow">Published reviews</div>'
            "<h1>Team alert reviews</h1><p>No weekly review has been published yet.</p></section>",
        )

    def volume(review: Review, schema: str) -> str:
        totals = review.totals.get(schema, SchemaTotals())
        if totals.events == 0 and totals.distinct_alerts == 0:
            return f'<span class="sub">no {schema} alerts</span>'
        return (
            f'<span class="vol"><b>{plural(totals.distinct_alerts, "distinct alert")}</b>'
            f'<span class="sub">{totals.events:,} events</span></span>'
        )

    rows = []
    for summary in teams:
        review = summary.latest
        attention = review.needs_attention
        rows.append(
            "<tr>"
            f'<td><a class="team" href="{h(team_url(review.team_id))}">{h(review.team_name)}</a></td>'
            f"<td>{h(phase_label(review.phase))}"
            f'<div class="sub">Phase-2 readiness {h(_readiness(review.readiness_pct))}</div></td>'
            f"<td>{volume(review, 'v1')}</td><td>{volume(review, 'v2')}</td>"
            f'<td class="num">{plural(attention, "alert") if attention else "none"}</td>'
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
    return layout("Team alert reviews", body)
