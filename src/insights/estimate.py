"""Time to retire v1 (spec section 7): a measured-pace date and a configured-effort figure."""

from __future__ import annotations

import math
from datetime import date, timedelta

from src.config.planning import DEFAULT_V1_RULE_EFFORT_DAYS, WORKING_DAYS_PER_WEEK
from src.insights.model import AlertRow, Estimate, SummaryInputs, WeekRules

_LOOKBACK_WEEKS = 3
_MIN_EARLIER_WEEKS = 2
_MIN_RETIRED = 2
_WEEK = timedelta(days=7)


def v1_rule_key(alert: AlertRow) -> str:
    """The unit of migration work: ``url:<alert_rule_url>`` or ``app:<application>``."""
    url = (alert.alert_rule_url or "").strip()
    if url:
        return f"url:{url}"
    return f"app:{alert.application}"


def _earlier_weeks(history: tuple[WeekRules, ...]) -> list[WeekRules]:
    """Up to three consecutive published weeks before the selected one, newest first."""
    earlier: list[WeekRules] = []
    for i in range(len(history) - 2, -1, -1):
        if len(earlier) == _LOOKBACK_WEEKS:
            break
        later = history[i + 1]
        if later.basis_changed or later.week_end - history[i].week_end != _WEEK:
            break
        earlier.append(history[i])
    return earlier


def estimate(inputs: SummaryInputs) -> Estimate:
    """Never stored, never in an output file. See spec 7.1 and 7.2."""
    rules_left = len({v1_rule_key(a) for a in inputs.alerts if a.schema == "v1"})
    override = inputs.v1_rule_effort_days
    per_rule = override if override is not None else DEFAULT_V1_RULE_EFFORT_DAYS
    effort_days = rules_left * per_rule
    effort_weeks = effort_days / WORKING_DAYS_PER_WEEK

    def build(
        *,
        lookback: int = 0,
        retired: int | None = None,
        pace: float | None = None,
        projected: date | None = None,
        reason: str | None = None,
    ) -> Estimate:
        return Estimate(
            rules_left=rules_left,
            lookback_weeks=lookback,
            retired=retired,
            pace_per_week=pace,
            projected_week_end=projected,
            no_estimate_reason=reason,
            effort_days_per_rule=per_rule,
            effort_is_override=override is not None,
            effort_days=effort_days,
            effort_weeks=effort_weeks,
        )

    if not inputs.published or not inputs.history:
        return build(
            reason=(
                "This week is not published, so there is no published history to "
                "measure a pace from."
            )
        )

    selected = inputs.history[-1]
    earlier = _earlier_weeks(inputs.history)
    if len(earlier) < _MIN_EARLIER_WEEKS:
        return build(
            lookback=len(earlier),
            reason=(
                f"Needs at least 2 earlier published weeks back to back; found {len(earlier)}."
            ),
        )

    seen: set[str] = set()
    for week in earlier:
        seen |= week.v1_rules
    retired = len(seen - selected.v1_rules)
    if retired < _MIN_RETIRED:
        return build(
            lookback=len(earlier),
            retired=retired,
            reason=(
                f"Fewer than 2 v1 alert rules stopped firing over the last {len(earlier)} "
                "weeks, too few to measure a pace."
            ),
        )

    pace = retired / len(earlier)
    if rules_left == 0:
        projected = selected.week_end.date()
    else:
        projected = (selected.week_end + _WEEK * math.ceil(rules_left / pace)).date()
    return build(lookback=len(earlier), retired=retired, pace=pace, projected=projected)
