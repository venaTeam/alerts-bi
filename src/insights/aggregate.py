"""Per-application rows, fire-frequency rows and the biggest single source (spec section 4)."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from src.domain.cadence import REPEAT_INTERVAL
from src.insights.model import AlertRow, AppRow, FireRow
from src.rules.catalogs import CORE_RULE_IDS

_DAY = timedelta(hours=24)


def _rule_number(rule_id: str) -> tuple[int, str]:
    digits = rule_id[1:]
    return (int(digits) if digits.isdigit() else 10**6, rule_id)


def by_application(alerts: tuple[AlertRow, ...]) -> tuple[AppRow, ...]:
    groups: dict[tuple[str, str], list[AlertRow]] = defaultdict(list)
    for alert in alerts:
        groups[(alert.application, alert.schema)].append(alert)
    rows: list[AppRow] = []
    for (application, schema), members in groups.items():
        rule_flagged = [a for a in members if a.quality_state == "rule_flagged"]
        llm_flagged = [a for a in members if a.quality_state == "llm_flagged"]
        rules = {rid for a in members for rid in a.core_rule_ids}
        rows.append(
            AppRow(
                application=application,
                schema=schema,
                alerts=len(members),
                rule_flagged_alerts=len(rule_flagged),
                llm_flagged_alerts=len(llm_flagged),
                events=sum(a.row_count for a in members),
                rule_flagged_events=sum(a.row_count for a in rule_flagged),
                llm_flagged_events=sum(a.row_count for a in llm_flagged),
                rules=tuple(sorted(rules, key=_rule_number)),
            )
        )
    rows.sort(
        key=lambda r: (
            -(r.rule_flagged_events + r.llm_flagged_events),
            -r.events,
            r.application,
            r.schema,
        )
    )
    return tuple(rows)


def fire_rows(alerts: tuple[AlertRow, ...]) -> tuple[FireRow, ...]:
    rows: list[FireRow] = []
    for alert in alerts:
        interval = REPEAT_INTERVAL[alert.schema]
        span = alert.last_seen - alert.first_seen + interval
        ratio = alert.row_count / (span / interval) if alert.provider == "grafana" else None
        rows.append(
            FireRow(
                alert=alert,
                span_hours=span.total_seconds() / 3600,
                ratio=ratio,
                events_per_24h=alert.row_count * (_DAY / span),
                pattern=alert.fire_pattern,
            )
        )
    rows.sort(
        key=lambda r: (
            -r.alert.row_count,
            r.alert.schema,
            r.alert.application,
            r.alert.key_field,
        )
    )
    return tuple(rows)


def biggest(alerts: tuple[AlertRow, ...]) -> AlertRow | None:
    if not alerts:
        return None
    return min(alerts, key=lambda a: (-a.row_count, a.schema, a.key_field))


def primary_rule_counts(alerts: tuple[AlertRow, ...], schema: str) -> tuple[tuple[str, int], ...]:
    """One schema's rule-flagged alerts partitioned by their primary rule.

    The primary rule is an alert's first core rule in catalogue order, so each alert counts
    once and the counts sum to the schema's rule-flagged alerts. ``(rule_id, alerts)`` pairs
    in catalogue order, zeros omitted. One schema only: v1 and v2 are never combined.
    """
    counts = dict.fromkeys(CORE_RULE_IDS, 0)
    for alert in alerts:
        if alert.schema != schema or alert.quality_state != "rule_flagged":
            continue
        primary = next((rid for rid in CORE_RULE_IDS if rid in alert.core_rule_ids), None)
        if primary is not None:
            counts[primary] += 1
    return tuple((rid, n) for rid, n in counts.items() if n)
