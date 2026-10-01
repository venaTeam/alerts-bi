"""What the portal's Summary section reads, and only through the ``portal_*`` views.

Like :mod:`src.portal.queries`, every statement here reads a view that holds published weeks
only, is parameterized, and orders its rows explicitly so the summary built from them is
deterministic. Nothing writes, and no version, run timing or hash is selected: the views do
not carry them, and ``basis_changed`` says only that the measurement basis moved between two
published weeks, never what it moved to.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from src.db.connection import Database
from src.insights import AlertRow, RuleTotal, SchemaTotals, SummaryInputs, WeekRules
from src.insights.estimate import v1_rule_key

__all__ = ["load_portal_summary"]

_SCHEMAS = ("v1", "v2")
#: The selected week plus the up to 3 earlier weeks of the estimate's lookback (spec 7.1).
#: Older weeks cannot change the estimate, so they are never read.
HISTORY_WEEKS = 4
_STATES = ("rule_flagged", "llm_flagged", "needs_review", "assessed_good", "unassessed")


def _int(value: Any) -> int:
    return int(value or 0)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _ids(value: Any) -> tuple[str, ...]:
    return tuple(part for part in str(value or "").split(",") if part)


def _schema_totals(rows: list[dict[str, Any]]) -> dict[str, SchemaTotals]:
    by_schema = {str(row["alert_schema"]): row for row in rows}
    totals: dict[str, SchemaTotals] = {}
    for schema in _SCHEMAS:
        row = by_schema.get(schema, {})
        totals[schema] = SchemaTotals(
            schema=schema,
            events=_int(row.get("events")),
            distinct_alerts=_int(row.get("distinct_alerts")),
            distinct_per_day=None,
            rule_flagged_events=_int(row.get("flagged_rows")),
            rule_flagged_alerts=_int(row.get("rule_flagged")),
            suppressed=_int(row.get("suppressed")),
            unseen=_optional_int(row.get("unseen")),
            unseen_alerts=_optional_int(row.get("unseen_alerts")),
            states={state: _int(row.get(state)) for state in _STATES},
            readiness_gaps=_int(row.get("readiness_gaps")),
        )
    return totals


def _alert(row: dict[str, Any]) -> AlertRow:
    unseen = row["unseen"]
    return AlertRow(
        schema=str(row["alert_schema"]),
        application=str(row["application"]),
        key_field=str(row["key_field"]),
        message=row["message"],
        severity=row["severity"],
        provider=row["provider"],
        alert_rule_url=row["alert_rule_url"],
        component=row["component"],
        node_name=row["node_name"],
        row_count=_int(row["row_count"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        quality_state=str(row["quality_state"]),
        core_rule_ids=_ids(row["core_rule_ids"]),
        readiness_rule_ids=_ids(row["readiness_rule_ids"]),
        llm_principle_id=row["llm_principle_id"],
        llm_confidence=row["llm_confidence"],
        clear_count=_int(row["clear_count"]),
        max_clear_cycles_24h=_int(row["max_clear_cycles_24h"]),
        fire_pattern=row["fire_pattern"],
        unseen=None if unseen is None else bool(unseen),
        max_episode_firing_rows=_int(row["max_episode_firing_rows"]),
        open_since=row["open_since"],
    )


def _rule_key(application: str, alert_rule_url: str | None, week_end: datetime) -> str:
    """The v1 rule key of one stored identity, through the one definition in src.insights."""
    return v1_rule_key(
        AlertRow(
            schema="v1",
            application=application,
            key_field="",
            message=None,
            severity=None,
            provider=None,
            alert_rule_url=alert_rule_url,
            component=None,
            node_name=None,
            row_count=0,
            first_seen=week_end,
            last_seen=week_end,
            quality_state="",
            core_rule_ids=(),
            readiness_rule_ids=(),
            llm_principle_id=None,
            llm_confidence=None,
            clear_count=0,
            max_clear_cycles_24h=0,
            fire_pattern=None,
            unseen=None,
        )
    )


def _history(db: Database, team_id: str, window_end: datetime) -> tuple[WeekRules, ...]:
    """The selected week and the published weeks just before it that the estimate can use.

    Published weeks of one team never overlap, so ``window_end`` orders them strictly.
    """
    newest_first = db.query(
        f"""
        SELECT TOP ({HISTORY_WEEKS}) run_id, window_end, basis_changed
        FROM portal_reviews
        WHERE team_id = :team_id AND window_end <= :window_end
        ORDER BY window_end DESC, run_id DESC
        """,
        {"team_id": team_id, "window_end": window_end},
    )
    weeks = list(reversed(newest_first))
    if not weeks:
        return ()
    run_params = {f"run{i}": str(week["run_id"]) for i, week in enumerate(weeks)}
    rules = db.query(
        f"""
        SELECT DISTINCT a.run_id, a.application, a.alert_rule_url
        FROM portal_alerts AS a
        WHERE a.alert_schema = 'v1' AND a.run_id IN ({", ".join(f":{name}" for name in run_params)})
        ORDER BY a.run_id ASC, a.application ASC, a.alert_rule_url ASC
        """,
        run_params,
    )
    ends = {str(week["run_id"]): week["window_end"] for week in weeks}
    keys: dict[str, set[str]] = defaultdict(set)
    for row in rules:
        run_id = str(row["run_id"])
        if run_id in ends:
            keys[run_id].add(
                _rule_key(str(row["application"]), row["alert_rule_url"], ends[run_id])
            )
    return tuple(
        WeekRules(
            week_end=week["window_end"],
            v1_rules=frozenset(keys.get(str(week["run_id"]), set())),
            basis_changed=bool(week["basis_changed"]),
        )
        for week in weeks
    )


def load_portal_summary(db: Database, team_id: str, run_id: str) -> SummaryInputs:
    """Build portal SummaryInputs for one published week from portal_* views only.
    history = the team's published weeks up to and including this one, oldest first,
    v1_rules = {v1_rule_key(...)} per week from portal_alerts where alert_schema = 'v1'.

    Only the weeks the estimate can reach are loaded: this one and the
    ``HISTORY_WEEKS - 1`` published weeks before it (spec section 7.1)."""
    review = db.query_one(
        """
        SELECT team_id, team_display_name, window_start, window_end, phase_derived,
               phase2_readiness_pct, v1_rule_effort_days
        FROM portal_reviews
        WHERE team_id = :team_id AND run_id = :run_id
        """,
        {"team_id": team_id, "run_id": run_id},
    )
    if review is None:
        raise LookupError("That week is not a published review of this team.")

    totals = db.query(
        """
        SELECT alert_schema, events, distinct_alerts, flagged_rows, rule_flagged, llm_flagged,
               needs_review, assessed_good, unassessed, readiness_gaps, suppressed, unseen,
               unseen_alerts
        FROM portal_schema_totals
        WHERE run_id = :run_id
        ORDER BY alert_schema ASC
        """,
        {"run_id": run_id},
    )
    rules = db.query(
        """
        SELECT alert_schema, rule_id, events, alerts
        FROM portal_rule_totals
        WHERE run_id = :run_id
        ORDER BY alert_schema ASC, rule_id ASC
        """,
        {"run_id": run_id},
    )
    alerts = db.query(
        """
        SELECT alert_schema, application, key_field, message, severity, provider,
               alert_rule_url, component, node_name, row_count, first_seen, last_seen,
               quality_state, core_rule_ids, readiness_rule_ids, llm_principle_id,
               llm_confidence, clear_count, max_clear_cycles_24h, fire_pattern, unseen,
               max_episode_firing_rows, open_since
        FROM portal_alerts
        WHERE run_id = :run_id
        ORDER BY alert_schema ASC, application ASC, key_field ASC
        """,
        {"run_id": run_id},
    )

    return SummaryInputs(
        surface="portal",
        team_id=str(review["team_id"]),
        display_name=str(review["team_display_name"]),
        window_start=review["window_start"],
        window_end=review["window_end"],
        phase=str(review["phase_derived"]),
        phase2_readiness_pct=_optional_float(review["phase2_readiness_pct"]),
        schemas=_schema_totals(totals),
        rules=tuple(
            RuleTotal(
                schema=str(row["alert_schema"]),
                rule_id=str(row["rule_id"]),
                events=_int(row["events"]),
                alerts=_int(row["alerts"]),
            )
            for row in rules
        ),
        alerts=tuple(_alert(row) for row in alerts),
        published=True,
        history=_history(db, team_id, review["window_end"]),
        v1_rule_effort_days=_optional_float(review["v1_rule_effort_days"]),
    )
