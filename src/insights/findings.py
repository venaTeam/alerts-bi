"""Key-findings sentences (spec section 4): templated from stored counts, never model text."""

from __future__ import annotations

from collections import defaultdict

from src.insights.model import AlertRow, KeyFinding, SummaryInputs

_CORE_RULES = ("R1", "R2", "R3", "R4", "R5", "R6", "R7")
_MAX_FINDINGS = 5


def _largest(inputs: SummaryInputs) -> KeyFinding | None:
    events: dict[str, int] = defaultdict(int)
    for rule in inputs.rules:
        if rule.rule_id in _CORE_RULES:
            events[rule.rule_id] += rule.events
    events = {rid: n for rid, n in events.items() if n > 0}
    if not events:
        return None
    rid = min(events, key=lambda r: (-events[r], r))
    # Describe the schema where the rule is biggest; v1 wins a tie.
    per_schema = {r.schema: r for r in inputs.rules if r.rule_id == rid}
    schema = min(per_schema, key=lambda s: (-per_schema[s].events, s))
    total = per_schema[schema]
    noun = "alert" if total.alerts == 1 else "alerts"
    body = f"{total.events:,} {schema} events from {total.alerts:,} {noun}."
    carriers = [a for a in inputs.alerts if a.schema == schema and rid in a.core_rule_ids]
    others = {tuple(sorted(set(a.core_rule_ids) - {rid})) for a in carriers}
    if carriers and len(others) == 1 and len(next(iter(others))) == 1:
        body += f" The same alerts also match {next(iter(others))[0]}."
    return KeyFinding("largest", f"{rid} is your largest finding", body, None, rid)


def _unassessed(inputs: SummaryInputs) -> KeyFinding | None:
    total = sum(s.states.get("unassessed", 0) for s in inputs.schemas.values())
    if total <= 0:
        return None
    return KeyFinding(
        "unassessed",
        f"{total:,} alerts could not be classified",
        "Unassessed should be zero. A non-zero count is a classifier failure, not a finding.",
        None,
        None,
    )


def _concentration(inputs: SummaryInputs) -> KeyFinding | None:
    # The schema with the most events; v1 wins a tie.
    schema = min(inputs.schemas, key=lambda s: (-inputs.schemas[s].events, s))
    alerts = sorted(
        (a for a in inputs.alerts if a.schema == schema),
        key=lambda a: (-a.row_count, a.application, a.key_field),
    )
    n = len(alerts)
    total = sum(a.row_count for a in alerts)
    if n <= 2 or total <= 0:
        return None
    acc = 0
    k = 0
    for alert in alerts:
        acc += alert.row_count
        k += 1
        if acc * 5 >= total * 4:
            break
    if k >= n:
        return None
    pct = int(100 * acc / total + 0.5)
    return KeyFinding(
        "concentration",
        f"{k} of {n} {schema} alerts make {pct}% of the events",
        f"They produced {acc:,} of {total:,} {schema} events this week.",
        None,
        None,
    )


def _hidden(inputs: SummaryInputs) -> KeyFinding | None:
    parts = [
        f"{inputs.schemas[s].suppressed:,} {s} events"
        for s in ("v1", "v2")
        if s in inputs.schemas and inputs.schemas[s].suppressed > 0
    ]
    if not parts:
        return None
    return KeyFinding(
        "hidden",
        "Your own panels hide alerts you still send",
        " and ".join(parts) + " match a filter in your dashboard (R5).",
        None,
        "R5",
    )


def _unseen(inputs: SummaryInputs) -> KeyFinding | None:
    shown = [s for s in inputs.schemas.values() if s.unseen is not None and s.unseen > 0]
    if not shown:
        return None
    parts = [
        f"{s.unseen_alerts or 0:,} {s.schema} alerts ({s.unseen or 0:,} events)"
        for s in sorted(shown, key=lambda t: t.schema)
    ]
    return KeyFinding(
        "unseen",
        "Some alerts reach none of your dashboards",
        " and ".join(parts) + " are outside every panel's narrowing.",
        "Widen a panel to include them, or confirm they are meant to stay out of view.",
        None,
    )


def _is_critical(alert: AlertRow) -> bool:
    return (alert.severity or "").lower() == "critical"


def _is_ready(alert: AlertRow) -> bool:
    gaps = alert.readiness_rule_ids
    if "R8" in gaps or "R10" in gaps:
        return False
    return not (_is_critical(alert) and "R9" in gaps)


def _readiness(inputs: SummaryInputs) -> KeyFinding | None:
    v2 = [a for a in inputs.alerts if a.schema == "v2"]
    if not v2:
        return None
    ready = sum(1 for a in v2 if _is_ready(a))
    critical = sum(1 for a in v2 if _is_critical(a) and "R9" in a.readiness_rule_ids)
    if critical == 0:
        body = "Every critical alert has a runbook."
    elif critical == 1:
        body = "1 critical alert has no runbook."
    else:
        body = f"{critical:,} critical alerts have no runbook."
    return KeyFinding(
        "readiness",
        f"{ready:,} of {len(v2):,} v2 alerts are phase-2 ready",
        body,
        "Add impact and an https:// runbook, starting with critical alerts.",
        None,
    )


def key_findings(inputs: SummaryInputs) -> tuple[KeyFinding, ...]:
    candidates = (
        _largest(inputs),
        _unassessed(inputs),
        _concentration(inputs),
        _hidden(inputs),
        _unseen(inputs),
        _readiness(inputs),
    )
    return tuple(f for f in candidates if f is not None)[:_MAX_FINDINGS]
