"""Everything the portal reads, and only through the ``portal_*`` views.

The views are the portal's whole world: its SQL login can read nothing else (design section
7.10). They contain published weeks only, so no query here needs to remember to filter out an
unpublished run - and a query that forgot could not see one anyway.

Nothing here writes, and nothing reaches Elasticsearch or the model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from src.db.connection import Database

__all__ = [
    "AlertDetail",
    "Review",
    "SchemaTotals",
    "TeamSummary",
    "WorklistPage",
    "alert_detail",
    "decisions_for",
    "list_teams",
    "team_reviews",
    "worklist",
]

Schema = Literal["v1", "v2"]
SCHEMAS: tuple[Schema, ...] = ("v1", "v2")


@dataclass(frozen=True, slots=True)
class SchemaTotals:
    events: int = 0
    distinct_alerts: int = 0
    flagged_rows: int = 0
    suppressed: int = 0
    rule_flagged: int = 0
    llm_flagged: int = 0
    needs_review: int = 0
    assessed_good: int = 0
    unassessed: int = 0
    readiness_gaps: int = 0
    needs_attention: int = 0


def _totals(row: dict[str, Any]) -> SchemaTotals:
    return SchemaTotals(**{name: int(row[name] or 0) for name in SchemaTotals.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class Review:
    """One published week of one team."""

    team_id: str
    team_name: str
    run_id: str
    window_start: datetime
    window_end: datetime
    published_at: datetime
    review_note: str | None
    phase: str
    readiness_pct: float | None
    totals: dict[str, SchemaTotals] = field(default_factory=dict)

    @property
    def week(self) -> date:
        """The URL key: the UTC date the week ends on. Unique per team because weeks never overlap."""
        return self.window_end.date()

    @property
    def needs_attention(self) -> int:
        return sum(t.needs_attention for t in self.totals.values())


@dataclass(frozen=True, slots=True)
class TeamSummary:
    latest: Review
    weeks: int


_REVIEW_COLUMNS = (
    "r.team_id, r.team_display_name, r.run_id, r.window_start, r.window_end, r.published_at, "
    "r.review_note, r.phase_derived, r.phase2_readiness_pct"
)
_TOTAL_COLUMNS = ", ".join(f"t.{name}" for name in SchemaTotals.__dataclass_fields__)


def _review(row: dict[str, Any], totals: dict[str, SchemaTotals]) -> Review:
    readiness = row["phase2_readiness_pct"]
    return Review(
        team_id=str(row["team_id"]),
        team_name=str(row["team_display_name"]),
        run_id=str(row["run_id"]),
        window_start=row["window_start"],
        window_end=row["window_end"],
        published_at=row["published_at"],
        review_note=row["review_note"],
        phase=str(row["phase_derived"]),
        readiness_pct=None if readiness is None else float(readiness),
        totals=totals,
    )


def _group_reviews(rows: list[dict[str, Any]]) -> list[Review]:
    """Fold one row per (review, schema) into one :class:`Review` with both schemas' totals."""
    ordered: dict[str, dict[str, Any]] = {}
    totals: dict[str, dict[str, SchemaTotals]] = {}
    for row in rows:
        run_id = str(row["run_id"])
        ordered.setdefault(run_id, row)
        totals.setdefault(run_id, {})[str(row["alert_schema"])] = _totals(row)
    return [_review(row, totals[run_id]) for run_id, row in ordered.items()]


def list_teams(db: Database) -> list[TeamSummary]:
    """Each team's latest published week, alphabetically - a directory, not a ranking."""
    rows = db.query(
        f"""
        WITH ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY window_end DESC) AS recency,
                   COUNT(*) OVER (PARTITION BY team_id) AS weeks
            FROM portal_reviews
        )
        SELECT {_REVIEW_COLUMNS}, r.weeks, t.alert_schema, {_TOTAL_COLUMNS}
        FROM ranked AS r
        JOIN portal_schema_totals AS t ON t.run_id = r.run_id
        WHERE r.recency = 1
        ORDER BY r.team_display_name ASC, r.team_id ASC, t.alert_schema ASC
        """
    )
    weeks = {str(row["run_id"]): int(row["weeks"]) for row in rows}
    return [TeamSummary(review, weeks[review.run_id]) for review in _group_reviews(rows)]


def team_reviews(db: Database, team_id: str) -> list[Review]:
    """Every published week of one team, oldest first."""
    rows = db.query(
        f"""
        SELECT {_REVIEW_COLUMNS}, t.alert_schema, {_TOTAL_COLUMNS}
        FROM portal_reviews AS r
        JOIN portal_schema_totals AS t ON t.run_id = r.run_id
        WHERE r.team_id = :team_id
        ORDER BY r.window_end ASC, t.alert_schema ASC
        """,
        {"team_id": team_id},
    )
    return _group_reviews(rows)


# ------------------------------------------------------------------ the work list


@dataclass(frozen=True, slots=True)
class AlertRow:
    alert_schema: str
    application: str
    key_field: str
    message: str | None
    severity: str | None
    component: str | None
    node_name: str | None
    environment: str | None
    provider: str | None
    alert_rule_url: str | None
    row_count: int
    first_seen: datetime
    last_seen: datetime
    core_rule_ids: tuple[str, ...]
    readiness_rule_ids: tuple[str, ...]
    evidence: dict[str, dict[str, Any]]
    quality_state: str
    llm_principle_id: str | None
    llm_confidence: str | None
    llm_justification: str | None
    impact: str | None
    runbook_url: str | None
    alert_status: str | None
    time_created: str | None
    representative_at: datetime
    attention_rank: int

    @property
    def needs_attention(self) -> bool:
        return self.attention_rank < 4

    @property
    def model_finding(self) -> str | None:
        """The principle the model raised, when it raised one."""
        if self.quality_state in ("llm_flagged", "needs_review") and self.llm_principle_id:
            return self.llm_principle_id
        return None


def _ids(value: Any) -> tuple[str, ...]:
    return tuple(part for part in str(value or "").split(",") if part)


def _evidence(value: Any) -> dict[str, dict[str, Any]]:
    try:
        entries = json.loads(str(value or "[]"))
    except ValueError:
        return {}
    if not isinstance(entries, list):
        return {}
    return {
        str(entry["rule_id"]): entry
        for entry in entries
        if isinstance(entry, dict) and "rule_id" in entry
    }


def _alert(row: dict[str, Any]) -> AlertRow:
    return AlertRow(
        alert_schema=str(row["alert_schema"]),
        application=str(row["application"]),
        key_field=str(row["key_field"]),
        message=row["message"],
        severity=row["severity"],
        component=row["component"],
        node_name=row["node_name"],
        environment=row["environment"],
        provider=row["provider"],
        alert_rule_url=row["alert_rule_url"],
        row_count=int(row["row_count"]),
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        core_rule_ids=_ids(row["core_rule_ids"]),
        readiness_rule_ids=_ids(row["readiness_rule_ids"]),
        evidence=_evidence(row["findings_evidence"]),
        quality_state=str(row["quality_state"]),
        llm_principle_id=row["llm_principle_id"],
        llm_confidence=row["llm_confidence"],
        llm_justification=row["llm_justification"],
        impact=row["impact"],
        runbook_url=row["runbook_url"],
        alert_status=row["alert_status"],
        time_created=row["time_created"],
        representative_at=row["representative_at"],
        attention_rank=int(row["attention_rank"]),
    )


_ALERT_COLUMNS = (
    "alert_schema, application, key_field, message, severity, component, node_name, "
    "environment, provider, alert_rule_url, row_count, first_seen, last_seen, "
    "core_rule_ids, readiness_rule_ids, findings_evidence, quality_state, llm_principle_id, "
    "llm_confidence, llm_justification, impact, runbook_url, alert_status, time_created, "
    "representative_at, attention_rank"
)


@dataclass(frozen=True, slots=True)
class WorklistPage:
    rows: list[AlertRow]
    total: int
    page: int
    page_size: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


def worklist(
    db: Database,
    run_id: str,
    *,
    attention_only: bool,
    schema: str | None,
    page: int,
    page_size: int,
) -> WorklistPage:
    """One page of a published week's alerts, paginated in SQL.

    Order: rule findings, advisory model findings, decisions needed, readiness-only gaps,
    then everything else; within a group by event count, then identity, so paging is stable.
    """
    where = ["run_id = :run_id"]
    params: dict[str, Any] = {"run_id": run_id}
    if attention_only:
        where.append("attention_rank < 4")
    if schema in SCHEMAS:
        where.append("alert_schema = :schema")
        params["schema"] = schema
    clause = " AND ".join(where)

    count = db.query_one(f"SELECT COUNT(*) AS total FROM portal_alerts WHERE {clause}", params)
    total = int(count["total"]) if count else 0
    pages = max(1, -(-total // page_size))
    page = min(max(page, 1), pages)

    rows = db.query(
        f"""
        SELECT {_ALERT_COLUMNS}
        FROM portal_alerts
        WHERE {clause}
        ORDER BY attention_rank ASC, row_count DESC, alert_schema ASC, application ASC,
                 key_field ASC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
        """,
        {**params, "offset": (page - 1) * page_size, "limit": page_size},
    )
    return WorklistPage([_alert(row) for row in rows], total, page, page_size)


# ------------------------------------------------------------------ one alert


@dataclass(frozen=True, slots=True)
class Decision:
    finding_id: str
    state: str
    note: str
    decided_at: datetime
    decided_by: str


@dataclass(frozen=True, slots=True)
class AlertDetail:
    alert: AlertRow
    #: Decision history per finding id, oldest first.
    decisions: dict[str, list[Decision]]


def decisions_for(
    db: Database, alert_schema: str, application: str, key_field: str
) -> dict[str, list[Decision]]:
    """Every decision on this exact identity, from any published week, oldest first."""
    rows = db.query(
        """
        SELECT finding_id, state, note, decided_at, decided_by
        FROM portal_decisions
        WHERE alert_schema = :alert_schema AND application = :application
          AND key_field = :key_field
        ORDER BY decided_at ASC, decision_id ASC
        """,
        {"alert_schema": alert_schema, "application": application, "key_field": key_field},
    )
    history: dict[str, list[Decision]] = {}
    for row in rows:
        history.setdefault(str(row["finding_id"]), []).append(
            Decision(
                finding_id=str(row["finding_id"]),
                state=str(row["state"]),
                note=str(row["note"]),
                decided_at=row["decided_at"],
                decided_by=str(row["decided_by"]),
            )
        )
    return history


def latest_decisions(
    db: Database, run_id: str, alerts: list[AlertRow]
) -> dict[tuple[str, str, str], dict[str, Decision]]:
    """The current decision per finding for a page of alerts, for the work-list badges."""
    if not alerts:
        return {}
    rows = db.query(
        """
        SELECT d.alert_schema, d.application, d.key_field, d.finding_id, d.state, d.note,
               d.decided_at, d.decided_by
        FROM portal_decisions AS d
        JOIN portal_alerts AS a
          ON a.alert_schema = d.alert_schema AND a.application = d.application
         AND a.key_field = d.key_field AND a.run_id = :run_id
        ORDER BY d.decided_at ASC, d.decision_id ASC
        """,
        {"run_id": run_id},
    )
    wanted = {(a.alert_schema, a.application, a.key_field) for a in alerts}
    latest: dict[tuple[str, str, str], dict[str, Decision]] = {}
    for row in rows:
        identity = (str(row["alert_schema"]), str(row["application"]), str(row["key_field"]))
        if identity not in wanted:
            continue
        latest.setdefault(identity, {})[str(row["finding_id"])] = Decision(
            finding_id=str(row["finding_id"]),
            state=str(row["state"]),
            note=str(row["note"]),
            decided_at=row["decided_at"],
            decided_by=str(row["decided_by"]),
        )
    return latest


def alert_detail(
    db: Database, run_id: str, alert_schema: str, application: str, key_field: str
) -> AlertDetail | None:
    row = db.query_one(
        f"""
        SELECT {_ALERT_COLUMNS}
        FROM portal_alerts
        WHERE run_id = :run_id AND alert_schema = :alert_schema
          AND application = :application AND key_field = :key_field
        """,
        {
            "run_id": run_id,
            "alert_schema": alert_schema,
            "application": application,
            "key_field": key_field,
        },
    )
    if row is None:
        return None
    return AlertDetail(_alert(row), decisions_for(db, alert_schema, application, key_field))
