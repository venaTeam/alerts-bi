"""The review portal, against a real disposable SQL Server database (design section 7.10).

Synthetic runs are persisted through the same ``persist_run`` the pipeline uses, published
through the operator functions, and read back by the portal using the application's SQL
credential. One test drives the real pipeline over the mock Elasticsearch
data, so the portal's totals are checked against numbers the pipeline itself stored.
"""

from __future__ import annotations

import dataclasses
import json
import re
import secrets
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from src.admin.summary import _published_history, basis_changes
from src.config import PortalSettings, SqlConfig, load_config
from src.db.connection import connect
from src.db.migrate import reset_test_database
from src.db.reader import grant_reader, read_only_problems
from src.db.repositories import PersistencePayload, RunIsPublished, persist_run
from src.insights import DailyPoint
from src.insights.labels import action
from src.insights.summary import summarize
from src.portal.app import build_portal
from src.portal.explain import EN_DASH
from src.portal.summary_queries import load_portal_summary
from src.review.decisions import DecisionRefused, record_decision
from src.review.publication import PublicationRefused, publish_run, unpublish_run

from tests.helpers.sql import sample_daily, sample_finding, sample_run

pytestmark = pytest.mark.integration

CONFIG = load_config()
DB = CONFIG.sql.test_database
TEAM = "portal-team"
OTHER = "publish-team"
WEEK = timedelta(hours=168)
W3 = datetime(2026, 8, 30, 16, 44, 35)
W2, W1 = W3 - WEEK, W3 - 2 * WEEK
LOCAL = ("10.20.30.40", 50000)

RUN = {name: name.ljust(64, "0") for name in ("wk1", "wk2", "wk3", "overlap", "unpub")}


def _run_id(name: str) -> str:
    return RUN[name]


# ------------------------------------------------------------------ fixture data


def _doc(**fields: Any) -> str:
    return json.dumps(fields, separators=(",", ":"))


def _evidence(*entries: tuple[str, int, dict[str, Any]]) -> str:
    return json.dumps(
        [
            {"rule_id": rule, "matched_rows": rows, "sample_evidence": sample}
            for rule, rows, sample in entries
        ]
    )


def _findings(run_id: str) -> list[dict[str, Any]]:
    """Eight identities covering every presentation case the portal has to get right."""
    base = {
        "run_id": run_id,
        "first_seen": W3 - timedelta(days=3),
        "last_seen": W3 - timedelta(hours=1),
    }
    return [
        # 1. The noisy generic message: rule findings, the biggest event count.
        sample_finding(
            **base,
            alert_schema="v1",
            application="notif-dispatcher",
            key_field="notif-dispatcher:dispatch-queue:notif-node-2",
            component="dispatch-queue",
            message="Something went wrong",
            severity="error",
            node_name="notif-node-2",
            provider="grafana",
            alert_rule_url=None,
            row_count=592,
            max_episode_firing_rows=3,
            open_since=W3 - timedelta(days=3),
            core_rule_ids="R1,R4",
            quality_state="rule_flagged",
            llm_principle_id=None,
            llm_confidence=None,
            llm_justification=None,
            findings_evidence=_evidence(
                ("R1", 592, {"field": "message", "normalized": "something went wrong"}),
                ("R4", 592, {"provider": "grafana", "alert_rule_url": None}),
            ),
            representative_doc=_doc(
                message="Something went wrong", time_created="2026-08-30T15:44:35Z"
            ),
        ),
        # 2. An EARLIER row matched R1; the latest row is a good message. The two must be
        #    labelled separately.
        sample_finding(
            **base,
            alert_schema="v1",
            application="checkout-svc",
            key_field="checkout-svc:cart:node-1",
            component="cart",
            message="Cart error rate above 2% over 5m on node-1",
            row_count=20,
            core_rule_ids="R1",
            quality_state="rule_flagged",
            llm_principle_id=None,
            llm_confidence=None,
            llm_justification=None,
            findings_evidence=_evidence(
                ("R1", 3, {"field": "message", "normalized": "error occurred"})
            ),
        ),
        # 3. High-confidence model finding: advisory.
        sample_finding(
            **base,
            alert_schema="v1",
            application="email-worker",
            key_field="email-worker:template-renderer:email-node-3",
            component="template-renderer",
            message="Unhandled exception in template renderer",
            row_count=412,
            quality_state="llm_flagged",
            llm_principle_id="P3",
            llm_confidence="high",
            llm_justification="Reports an exception but not which send path failed.",
        ),
        # 4. Medium-confidence model finding: a person decides.
        sample_finding(
            **base,
            alert_schema="v2",
            application="push-gateway",
            key_field="7e21c0d94ab35f68",
            component="token-cleanup",
            message="Nightly token cleanup finished, 0 tokens removed",
            severity="warning",
            environment="production",
            provider="api",
            alert_rule_url=None,
            row_count=7,
            quality_state="needs_review",
            llm_principle_id="P2",
            llm_confidence="medium",
            llm_justification="Reports a completed job; may be a log.",
            representative_doc=_doc(
                impact="Stale tokens accumulate",
                runbook_url="https://runbooks.internal/tokens",
                status="resolved",
            ),
        ),
        # 5. Readiness gaps only, critical: blocks phase 2. Hostile text and links.
        sample_finding(
            **base,
            alert_schema="v2",
            application="sms-gateway",
            key_field="958e442f8ad32241",
            component="sms-send",
            message='<script>alert("x")</script> SMS failure rate above 3%',
            severity="critical",
            environment="production",
            provider="grafana",
            alert_rule_url="javascript:alert(1)",
            row_count=2,
            readiness_rule_ids="R8,R9",
            findings_evidence=_evidence(
                ("R8", 2, {"reason": "missing"}),
                ("R9", 2, {"severity": "critical", "blocks_completion": True, "reason": "missing"}),
            ),
            representative_doc=_doc(runbook_url="javascript:alert(2)", status="firing"),
        ),
        # 6. The same alert after the team enriched it: a NEW v2 key.
        sample_finding(
            **base,
            alert_schema="v2",
            application="sms-gateway",
            key_field="41b8e07c2d9a6f13",
            component="sms-send",
            message="SMS failure rate above 3%",
            severity="critical",
            environment="production",
            row_count=1,
            representative_doc=_doc(
                impact="Users cannot sign in",
                runbook_url="https://runbooks.internal/sms",
                status="firing",
            ),
        ),
        # 7 and 8. Nothing to do: only in "All alerts".
        sample_finding(
            **base,
            alert_schema="v1",
            application="queue",
            key_field="queue:depth:n1",
            component="depth",
            row_count=88,
        ),
        sample_finding(
            **base,
            alert_schema="v2",
            application="queue",
            key_field="aa11bb22cc33dd44",
            component="depth",
            row_count=3,
            environment="production",
        ),
    ]


def _daily(run_id: str, team: str, end: datetime) -> list[dict[str, Any]]:
    rows = []
    for schema, alerts in (("v1", (400, 314, 400)), ("v2", (5, 4, 4))):
        for offset, count in enumerate(alerts):
            day = (end - timedelta(days=offset + 1)).date()
            rows.append(
                sample_daily(
                    run_id=run_id,
                    team_id=team,
                    alert_schema=schema,
                    snapshot_date=day.isoformat(),
                    bucket_start=datetime.combine(day, datetime.min.time()),
                    bucket_end=datetime.combine(day, datetime.min.time()) + timedelta(days=1),
                    alerts=count,
                    distinct_alerts=min(count, 4),
                    flagged_by_rule=count // 2,
                    suppressed=1 if schema == "v1" else 0,
                )
            )
    return rows


#: Per-rule events over the three dates `_daily` writes, matching the rich findings: R1 on
#: the generic message (592) and on an earlier row of checkout-svc (3); R4 on the generic
#: message; R8 and R9 on the critical sms-gateway alert.
_RULE_EVENTS = {
    ("v1", "R1"): (200, 198, 197),
    ("v1", "R4"): (200, 196, 196),
    ("v2", "R8"): (1, 1, 0),
    ("v2", "R9"): (1, 1, 0),
}


def _rule_counts(run_id: str, team: str, end: datetime) -> list[dict[str, Any]]:
    rows = []
    for (schema, rule), events in _RULE_EVENTS.items():
        for offset, count in enumerate(events):
            if count:
                rows.append(
                    {
                        "run_id": run_id,
                        "team_id": team,
                        "alert_schema": schema,
                        "snapshot_date": (end - timedelta(days=offset + 1)).date().isoformat(),
                        "rule_id": rule,
                        "ruleset_version": "1.0.0",
                        "match_count": count,
                        "distinct_count": 1,
                    }
                )
    return rows


def _store(name: str, team: str, end: datetime, *, rich: bool = False) -> None:
    run_id = _run_id(name)
    payload = PersistencePayload(
        run=sample_run(
            run_id=run_id,
            team_id=team,
            team_display_name="Portal Team" if team == TEAM else "Publish Team",
            run_at=end,
            window_start=end - WEEK,
            window_end=end,
        ),
        daily_metrics=_daily(run_id, team, end),
        rule_counts=_rule_counts(run_id, team, end) if rich else [],
        findings=_findings(run_id)
        if rich
        else [sample_finding(run_id=run_id, key_field=f"{name}:only", row_count=5)],
    )
    with connect(CONFIG.sql, DB) as db:
        persist_run(db, payload)


@pytest.fixture(scope="module")
def reader() -> SqlConfig:
    """A fresh database with three published weeks and an optional view-only login."""
    try:
        reset_test_database(CONFIG.sql, DB)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"SQL Server is not reachable: {exc}")

    _store("wk1", TEAM, W1, rich=True)
    _store("wk2", TEAM, W2, rich=True)
    _store("wk3", TEAM, W3, rich=True)
    _store("overlap", TEAM, W3 - timedelta(hours=49), rich=True)
    _store("unpub", TEAM, W3 + WEEK, rich=True)
    with connect(CONFIG.sql, DB) as db:
        for name, note in (("wk1", None), ("wk2", None), ("wk3", "Heartbeats are still hidden.")):
            publish_run(db, _run_id(name), published_by="operator", note=note)
        record_decision(
            db,
            run_id=_run_id("wk3"),
            alert_schema="v1",
            application="notif-dispatcher",
            key_field="notif-dispatcher:dispatch-queue:notif-node-2",
            finding_id="R1",
            state="pending",
            note="Raised in the review meeting.",
            decided_by="operator",
            now=W3 + timedelta(days=1),
        )
        record_decision(
            db,
            run_id=_run_id("wk3"),
            alert_schema="v1",
            application="notif-dispatcher",
            key_field="notif-dispatcher:dispatch-queue:notif-node-2",
            finding_id="R1",
            state="confirmed",
            note="Team agreed to rewrite it.",
            decided_by="operator",
            now=W3 + timedelta(days=2),
        )
        record_decision(
            db,
            run_id=_run_id("wk3"),
            alert_schema="v2",
            application="sms-gateway",
            key_field="958e442f8ad32241",
            finding_id="R9",
            state="confirmed",
            note="Runbook is being written.",
            decided_by="operator",
        )

    login = "alerts_bi_portal_test"
    password = "Rd!" + secrets.token_hex(12) + "aZ9"
    grant_reader(CONFIG.sql, DB, login, password)
    return dataclasses.replace(CONFIG.sql, user=login, password=password)


@pytest.fixture(scope="module")
def portal(reader: SqlConfig) -> Iterator[TestClient]:
    settings = PortalSettings(sql=CONFIG.sql, database=DB, page_size=3)
    with TestClient(build_portal(settings), client=LOCAL) as client:
        yield client


# ------------------------------------------------------------------ the credential


def test_the_optional_reader_login_is_limited_to_views(reader: SqlConfig) -> None:
    with connect(reader, DB) as db:
        assert read_only_problems(db) == []


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT TOP 1 * FROM runs",
        "SELECT TOP 1 representative_doc FROM alert_findings",
        "SELECT TOP 1 request_payload FROM llm_batch_attempts",
        "INSERT INTO finding_decisions (team_id, run_id, alert_schema, application, key_field, "
        "finding_id, state, note, decided_at, decided_by) VALUES ('t','r','v1','a','k','R1',"
        "'confirmed','n',SYSUTCDATETIME(),'x')",
        "UPDATE review_publications SET review_note = 'x'",
        "DELETE FROM portal_reviews",
    ],
)
def test_the_reader_cannot_write_or_read_past_the_views(reader: SqlConfig, statement: str) -> None:
    with connect(reader, DB) as db, pytest.raises(DBAPIError):
        db.execute(statement, {})


def test_the_reader_sees_only_published_weeks(reader: SqlConfig) -> None:
    with connect(reader, DB) as db:
        visible = {
            str(row["run_id"])
            for row in db.query("SELECT run_id FROM portal_reviews WHERE team_id = :t", {"t": TEAM})
        }
        alerts = {
            str(row["run_id"])
            for row in db.query("SELECT DISTINCT run_id FROM portal_alerts")
            if str(row["run_id"]).startswith(("wk", "overlap", "unpub"))
        }
    assert visible == {_run_id("wk1"), _run_id("wk2"), _run_id("wk3")}
    assert alerts == visible


def test_the_daily_view_holds_published_weeks_only_with_the_stored_values(
    reader: SqlConfig,
) -> None:
    columns = (
        "alert_schema, snapshot_date, covered_hours, distinct_alerts, flagged_by_rule_distinct"
    )
    with connect(CONFIG.sql, DB) as owner:
        applied = owner.query_one(
            "SELECT version FROM schema_migrations WHERE version = '007_portal_daily'", {}
        )
        stored = owner.query(
            f"SELECT {columns} FROM daily_metrics WHERE run_id = :r "
            "ORDER BY alert_schema, snapshot_date",
            {"r": _run_id("wk3")},
        )
    assert applied is not None, "migration 007 applied"
    with connect(reader, DB) as db:
        visible = {
            str(row["run_id"])
            for row in db.query("SELECT DISTINCT run_id FROM portal_daily_metrics", {})
            if str(row["run_id"]).startswith(("wk", "overlap", "unpub"))
        }
        viewed = db.query(
            f"SELECT {columns} FROM portal_daily_metrics WHERE run_id = :r "
            "ORDER BY alert_schema, snapshot_date",
            {"r": _run_id("wk3")},
        )
    assert visible == {_run_id("wk1"), _run_id("wk2"), _run_id("wk3")}
    assert stored and viewed == stored
    loaded = load_portal_summary_daily(reader, _run_id("wk3"))
    assert [(p.alert_schema, p.day) for p in loaded] == [
        (str(row["alert_schema"]), row["snapshot_date"]) for row in stored
    ]
    assert [p.rule_flagged_distinct for p in loaded] == [
        int(row["flagged_by_rule_distinct"]) for row in stored
    ]


def load_portal_summary_daily(reader: SqlConfig, run_id: str) -> tuple[DailyPoint, ...]:
    with connect(reader, DB) as db:
        return load_portal_summary(db, TEAM, run_id).daily


def test_the_episode_facts_reach_the_store_and_the_alerts_view(reader: SqlConfig) -> None:
    query = (
        "SELECT max_episode_firing_rows, open_since FROM {table} "
        "WHERE run_id = :r AND application = :a"
    )
    with connect(CONFIG.sql, DB) as owner:
        stored = owner.query(
            query.format(table="alert_findings"), {"r": _run_id("wk1"), "a": "notif-dispatcher"}
        )
        untouched = owner.query(
            query.format(table="alert_findings"), {"r": _run_id("wk1"), "a": "checkout-svc"}
        )
    assert stored[0]["max_episode_firing_rows"] == 3
    assert stored[0]["open_since"] == W3 - timedelta(days=3), "stored as naive UTC"
    assert (untouched[0]["max_episode_firing_rows"], untouched[0]["open_since"]) == (0, None)
    with connect(reader, DB) as db:
        viewed = db.query(
            query.format(table="portal_alerts"), {"r": _run_id("wk1"), "a": "notif-dispatcher"}
        )
    assert viewed[0]["max_episode_firing_rows"] == 3
    assert viewed[0]["open_since"] == W3 - timedelta(days=3)


def test_the_views_do_not_expose_the_complete_source_document(reader: SqlConfig) -> None:
    with connect(reader, DB) as db:
        columns = {
            str(row["name"])
            for row in db.query(
                "SELECT c.name FROM sys.columns c JOIN sys.views v ON v.object_id = c.object_id "
                "WHERE v.name LIKE 'portal[_]%'"
            )
        }
    assert "representative_doc" not in columns
    assert "request_payload" not in columns
    assert "registry_entry_snapshot" not in columns


# ------------------------------------------------------------------ publication rules


def test_an_overlapping_run_is_never_published() -> None:
    with connect(CONFIG.sql, DB) as db, pytest.raises(PublicationRefused, match="overlaps"):
        publish_run(db, _run_id("overlap"), published_by="operator", allow_gap=True)


def test_a_published_run_cannot_be_re_persisted_underneath_its_readers() -> None:
    with pytest.raises(RunIsPublished, match="unpublish"):
        _store("wk3", TEAM, W3, rich=True)


def test_publishing_replacing_and_withdrawing_one_teams_weeks() -> None:
    names = {"p1": W1, "p3": W3, "p3b": W3}
    for name, end in names.items():
        RUN[name] = name.ljust(64, "1")
        _store(name, OTHER, end)

    with connect(CONFIG.sql, DB) as db:
        publish_run(db, _run_id("p1"), published_by="op")
        with pytest.raises(PublicationRefused, match="gap"):
            publish_run(db, _run_id("p3"), published_by="op")
        publish_run(db, _run_id("p3"), published_by="op", allow_gap=True)

        with pytest.raises(PublicationRefused, match="--replace"):
            publish_run(db, _run_id("p3b"), published_by="op", allow_gap=True)
        result = publish_run(db, _run_id("p3b"), published_by="op", replace=True)
        assert result.replaced_run_id == _run_id("p3")

        current = {
            str(row["run_id"])
            for row in db.query(
                "SELECT run_id FROM review_publications WHERE team_id = :t AND withdrawn_at IS NULL",
                {"t": OTHER},
            )
        }
        assert current == {_run_id("p1"), _run_id("p3b")}
        withdrawn = db.query_one(
            "SELECT withdrawn_reason FROM review_publications WHERE run_id = :r",
            {"r": _run_id("p3")},
        )
        assert withdrawn is not None and "replaced by run" in str(withdrawn["withdrawn_reason"])

        unpublish_run(db, _run_id("p1"), withdrawn_by="op", reason="published in error")
        with pytest.raises(PublicationRefused, match="not currently published"):
            unpublish_run(db, _run_id("p1"), withdrawn_by="op", reason="again")

        # Once withdrawn, the run may be re-persisted again: its audit row survives.
        _store("p1", OTHER, W1)
        assert db.query_one(
            "SELECT COUNT(*) AS n FROM review_publications WHERE run_id = :r", {"r": _run_id("p1")}
        ) == {"n": 1}


# ------------------------------------------------------------------ decisions


def test_decisions_are_append_only() -> None:
    with (
        connect(CONFIG.sql, DB) as db,
        pytest.raises(DBAPIError, match="append-only"),
        db.transaction(),
    ):
        db.execute("UPDATE finding_decisions SET state = 'dismissed'", {})
    with (
        connect(CONFIG.sql, DB) as db,
        pytest.raises(DBAPIError, match="append-only"),
        db.transaction(),
    ):
        db.execute("DELETE FROM finding_decisions", {})


def test_a_decision_must_name_a_finding_the_alert_actually_has() -> None:
    with (
        connect(CONFIG.sql, DB) as db,
        pytest.raises(DecisionRefused, match="its findings are: R1, R4"),
    ):
        record_decision(
            db,
            run_id=_run_id("wk3"),
            alert_schema="v1",
            application="notif-dispatcher",
            key_field="notif-dispatcher:dispatch-queue:notif-node-2",
            finding_id="R2",
            state="confirmed",
            note="x",
            decided_by="op",
        )


def test_a_decision_is_only_made_on_a_published_week() -> None:
    with (
        connect(CONFIG.sql, DB) as db,
        pytest.raises(DecisionRefused, match="not a published week"),
    ):
        record_decision(
            db,
            run_id=_run_id("unpub"),
            alert_schema="v1",
            application="notif-dispatcher",
            key_field="notif-dispatcher:dispatch-queue:notif-node-2",
            finding_id="R1",
            state="confirmed",
            note="x",
            decided_by="op",
        )


def test_a_decision_never_changes_the_pipelines_verdict() -> None:
    with connect(CONFIG.sql, DB) as db:
        row = db.query_one(
            "SELECT quality_state, llm_principle_id FROM alert_findings WHERE run_id = :r "
            "AND key_field = 'notif-dispatcher:dispatch-queue:notif-node-2'",
            {"r": _run_id("wk3")},
        )
    assert row == {"quality_state": "rule_flagged", "llm_principle_id": None}


# ------------------------------------------------------------------ the pages


def test_the_directory_lists_published_teams_without_service_internals(portal: TestClient) -> None:
    page = portal.get("/").text
    assert "Portal Team" in page
    assert "3" in page  # weeks reviewed
    for internal in (_run_id("wk3"), "registry", "ruleset", "fake-model", "1.0.0"):
        assert internal not in page


def test_totals_match_the_stored_metrics_and_keep_v1_and_v2_apart(
    portal: TestClient, reader: SqlConfig
) -> None:
    with connect(CONFIG.sql, DB) as db:
        stored = {
            str(row["alert_schema"]): (int(row["events"]), int(row["distinct_alerts"]))
            for row in db.query(
                """
                SELECT d.alert_schema, d.events, f.distinct_alerts FROM
                  (SELECT alert_schema, SUM(alerts) AS events FROM daily_metrics
                   WHERE run_id = :r GROUP BY alert_schema) AS d
                JOIN (SELECT alert_schema, COUNT(*) AS distinct_alerts FROM alert_findings
                   WHERE run_id = :r GROUP BY alert_schema) AS f ON f.alert_schema = d.alert_schema
                """,
                {"r": _run_id("wk3")},
            )
        }
    with connect(reader, DB) as db:
        totals = {
            str(row["alert_schema"]): (int(row["events"]), int(row["distinct_alerts"]))
            for row in db.query(
                "SELECT alert_schema, events, distinct_alerts FROM portal_schema_totals WHERE run_id = :r",
                {"r": _run_id("wk3")},
            )
        }
    assert totals == stored == {"v1": (1114, 4), "v2": (13, 4)}

    page = portal.get(f"/teams/{TEAM}").text
    assert _cards(page) == [(4, 1114), (4, 13)]
    assert "per day" not in page
    assert "1,127" not in page, "v1 and v2 events are never added together"


def _cards(page: str) -> list[tuple[int, int]]:
    """(alerts, events) from each Overview card, v1 then v2."""
    return [
        (int(alerts.replace(",", "")), int(events.replace(",", "")))
        for alerts, events in re.findall(
            r'<span class="big">([\d,]+)</span><span class="sub">alerts</span></div>'
            r'<div><span class="big">([\d,]+)</span><span class="sub">events</span>',
            page,
        )
    ]


def test_the_latest_week_is_the_default_and_every_week_is_addressable(portal: TestClient) -> None:
    latest = portal.get(f"/teams/{TEAM}").text
    assert "Heartbeats are still hidden." in latest
    assert f"23 Aug {EN_DASH} 30 Aug 2026" in latest
    assert portal.get(f"/teams/{TEAM}/weeks/{W1.date()}").status_code == 200
    assert portal.get(f"/teams/{TEAM}/weeks/{(W3 + WEEK).date()}").status_code == 404, "unpublished"
    assert portal.get("/teams/nobody").status_code == 404
    picked = portal.get(
        f"/teams/{TEAM}/weeks", params={"week": str(W2.date())}, follow_redirects=False
    )
    assert picked.status_code == 303 and picked.headers["location"].endswith(str(W2.date()))


def _tab(portal: TestClient, tab: str, week: datetime = W3, **params: Any) -> str:
    path = f"/teams/{TEAM}/weeks/{week.date()}" + (f"/{tab}" if tab else "")
    response = portal.get(path, params=params)
    assert response.status_code == 200, (tab, response.text)
    return str(response.text)


def test_every_tab_is_addressable_and_renders_without_internals(portal: TestClient) -> None:
    for tab in ("", "fix", "volume", "dashboards", "migration", "history", "slides"):
        page = _tab(portal, tab)
        assert 'class="tab on"' in page, tab
        for internal in (_run_id("wk3"), "registry", "ruleset", "per day", "fake-model"):
            assert internal not in page, (tab, internal)
        text = re.sub(r"<[^>]+>", " ", page)
        assert not re.search(r"\bR(10|[1-9])\b", text), (tab, "no rule id is shown")
    assert portal.get(f"/teams/{TEAM}/weeks/{W3.date()}/nonsense").status_code == 404


def test_the_week_menu_keeps_the_tab(portal: TestClient) -> None:
    page = _tab(portal, "dashboards")
    for week in (W1, W2, W3):
        assert f'href="/teams/{TEAM}/weeks/{week.date()}/dashboards"' in page
    assert "<select" not in page


def test_history_has_one_point_per_published_week(portal: TestClient) -> None:
    page = _tab(portal, "history")
    # Two schemas x two measures, three contiguous weeks each.
    assert page.count("<polyline") == 4
    assert page.count('class="dot v1') == 6 and page.count('class="dot v2') == 6
    assert page.count(">Open</a>") == 2 and ">Viewing<" in page
    assert "<polyline" not in _tab(portal, ""), "the Overview draws no history"


def _alerts(page: str) -> str:
    """The alert list alone: the table above it names the same problems."""
    return page[page.index('id="alerts"') :]


def test_the_work_list_is_ordered_and_paginated_in_sql(portal: TestClient) -> None:
    first = _alerts(_tab(portal, "fix"))
    assert 'Alerts <span class="sub">5</span>' in first
    assert "1&ndash;3 of 5" in first
    order = [
        first.index(text)
        for text in ("Something went wrong", "Cart error rate", "Unhandled exception")
    ]
    assert order == sorted(order), "rule findings by event count, then model findings"

    second = _alerts(_tab(portal, "fix", page=2))
    assert "4&ndash;5 of 5" in second
    assert "Nightly token cleanup" in second and "Something went wrong" not in second

    everything = _alerts(_tab(portal, "fix", show="all", schema="v2", page=2))
    assert "4&ndash;4 of 4" in everything


def test_the_stored_weekly_totals_reach_the_overview_and_the_fix_list(
    portal: TestClient, reader: SqlConfig
) -> None:
    with connect(reader, DB) as db:
        schemas = {
            str(row["alert_schema"]): (int(row["distinct_alerts"]), int(row["events"]))
            for row in db.query(
                "SELECT alert_schema, distinct_alerts, events FROM portal_schema_totals "
                "WHERE run_id = :r",
                {"r": _run_id("wk3")},
            )
        }
        rules = {
            (str(row["alert_schema"]), str(row["rule_id"])): (
                int(row["events"]),
                int(row["alerts"]),
            )
            for row in db.query(
                "SELECT alert_schema, rule_id, events, alerts FROM portal_rule_totals "
                "WHERE run_id = :r",
                {"r": _run_id("wk3")},
            )
        }
    assert rules == {
        ("v1", "R1"): (595, 2),
        ("v1", "R4"): (592, 1),
        ("v2", "R8"): (2, 1),
        ("v2", "R9"): (2, 1),
    }
    assert _cards(_tab(portal, "")) == [schemas["v1"], schemas["v2"]]

    page = _tab(portal, "fix")
    table = page[page.index("What to change") : page.index('id="alerts"')]

    def cells(rule: str) -> list[str]:
        row = table[table.index(f'<span class="item">{action(rule)}') :]
        return re.findall(r'<td class="num r">(.*?)</td>', row[: row.index("</tr>")])

    assert cells("R1") == [
        '2 alerts<span class="sub">595 events</span>',
        '<span class="sub">—</span>',
    ]
    assert cells("R4") == [
        '1 alert<span class="sub">592 events</span>',
        '<span class="sub">—</span>',
    ]
    assert cells("R8") == ['<span class="sub">—</span>', "1 alert"]
    assert table.index(action("R1")) < table.index(action("R4")), "most events first"
    assert f'href="/teams/{TEAM}/weeks/2026-08-30/fix?rule=R1#alerts"' in table


def test_dashboards_say_not_measured_rather_than_zero(portal: TestClient) -> None:
    page = _tab(portal, "dashboards")
    unseen = page[page.index("On no dashboard") :]
    assert "Not measured this week." in unseen, "no panel for either schema"
    assert "None found" not in unseen


def test_the_slides_tab_holds_the_two_presentation_slides(portal: TestClient) -> None:
    page = _tab(portal, "slides")
    slides = page[page.index('<section class="slides"') :]
    assert slides.count('<section class="slide ') == 2
    assert "stands</h4>" in slides and "The week, day by day</h4>" in slides
    assert slides.count('<svg class="sl-chart"') == 2
    assert "week ending 30 Aug 2026 · Alerts BI" in slides
    assert _run_id("wk3") not in slides and "href=" not in slides


def test_the_work_list_filters_by_state_and_problem(portal: TestClient) -> None:
    listing = _alerts(_tab(portal, "fix", show="all", state="rule_flagged", rule="R1"))
    assert "1&ndash;2 of 2" in listing and 'Alerts <span class="sub">2</span>' in listing
    assert "Something went wrong" in listing and "Cart error rate" in listing
    for other in ("Unhandled exception", "Nightly token cleanup", "SMS failure rate"):
        assert other not in listing, other
    banner = _tab(portal, "fix", rule="R1")
    assert action("R1") in banner and "<b>Fix:</b>" in banner and "What to change" not in banner

    readiness = _alerts(_tab(portal, "fix", rule="R9"))
    assert "1&ndash;1 of 1" in readiness and "SMS failure rate" in readiness

    nothing = _tab(portal, "fix", state="assessed_good", rule="R1")
    assert "Nothing matches." in nothing

    fix = f"/teams/{TEAM}/weeks/{W3.date()}/fix"
    assert portal.get(fix, params={"rule": "R11"}).status_code == 422
    assert portal.get(fix, params={"state": "bad"}).status_code == 422


def test_old_work_list_links_open_the_fix_list(portal: TestClient) -> None:
    moved = portal.get(f"/teams/{TEAM}", params={"rule": "R1"}, follow_redirects=False)
    assert moved.status_code == 303
    assert moved.headers["location"] == f"/teams/{TEAM}/weeks/2026-08-30/fix?rule=R1#alerts"
    week = portal.get(
        f"/teams/{TEAM}/weeks/{W2.date()}", params={"show": "all"}, follow_redirects=False
    )
    assert week.headers["location"].endswith(f"/weeks/{W2.date()}/fix?show=all#alerts")
    picked = portal.get(
        f"/teams/{TEAM}/weeks",
        params={"week": str(W2.date()), "tab": "history"},
        follow_redirects=False,
    )
    assert picked.headers["location"].endswith(f"/weeks/{W2.date()}/history")


def test_paging_keeps_the_filters(portal: TestClient) -> None:
    listing = _alerts(_tab(portal, "fix", show="all", state="assessed_good"))
    assert "1&ndash;3 of 4" in listing
    assert "show=all&amp;state=assessed_good&amp;page=2#alerts" in listing
    second = _alerts(_tab(portal, "fix", show="all", state="assessed_good", page=2))
    assert "4&ndash;4 of 4" in second


PACE_TEAM = "pace-team"
#: Four back-to-back published weeks whose v1 rules shrink a, b, c, d -> a.
PACE_WEEKS = {
    # Older than the lookback of the latest week: never read for it.
    "pace0": (W1 - 2 * WEEK, "abcdefgh"),
    "pace1": (W1 - WEEK, "abcd"),
    "pace2": (W1, "abc"),
    "pace3": (W2, "ab"),
    "pace4": (W3, "a"),
}


def _pace_run(name: str, end: datetime, rules: str, *, effort: float | None = None) -> None:
    RUN[name] = name.ljust(64, "2")
    run_id = _run_id(name)
    snapshot: dict[str, Any] = {"team_id": PACE_TEAM}
    if effort is not None:
        snapshot["planning"] = {"v1_rule_effort_days": effort}
    findings = [
        sample_finding(
            run_id=run_id,
            alert_schema="v1",
            application=f"app-{rule}",
            key_field=f"app-{rule}:c:n",
            alert_rule_url=f"https://grafana.internal/rules/{rule}",
            row_count=10,
            first_seen=end - timedelta(days=2),
            last_seen=end - timedelta(hours=1),
        )
        for rule in rules
    ]
    with connect(CONFIG.sql, DB) as db:
        persist_run(
            db,
            PersistencePayload(
                run=sample_run(
                    run_id=run_id,
                    team_id=PACE_TEAM,
                    team_display_name="Pace Team",
                    run_at=end,
                    window_start=end - WEEK,
                    window_end=end,
                    registry_entry_snapshot=json.dumps(snapshot),
                ),
                daily_metrics=_daily(run_id, PACE_TEAM, end),
                findings=findings,
            ),
        )


def test_the_estimate_is_drawn_from_published_weeks_only(portal: TestClient) -> None:
    for name, (end, rules) in PACE_WEEKS.items():
        _pace_run(name, end, rules, effort=2.0 if name == "pace4" else None)
    # An unpublished run overlapping an earlier week, firing rules nobody published. Were it
    # read, four more rules would count as retired and the pace would change.
    _pace_run("pace-shadow", W2 - timedelta(hours=49), "wxyz")
    with connect(CONFIG.sql, DB) as db:
        for name in PACE_WEEKS:
            publish_run(db, _run_id(name), published_by="operator")

    with connect(CONFIG.sql, DB) as db:
        latest = summarize(load_portal_summary(db, PACE_TEAM, _run_id("pace4")))
        earliest = summarize(load_portal_summary(db, PACE_TEAM, _run_id("pace1")))
    # Three earlier weeks back to back; b, c and d stopped firing: pace 1 rule a week, and
    # the one rule left projects one week past the selected week.
    estimate = latest.estimate
    assert estimate.projected_week_end == date(2026, 9, 6)
    assert (estimate.retired, estimate.lookback_weeks, estimate.pace_per_week) == (3, 3, 1.0)
    assert (estimate.effort_days, estimate.effort_is_override) == (2.0, True)
    # Only the selected week and the three before it are read: the oldest week's extra
    # rules (e..h) would otherwise be there to retire.
    assert [week.week_end for week in latest.inputs.history] == [W1 - WEEK, W1, W2, W3]

    # An earlier week sees only the weeks before it, never a later one: one earlier week.
    assert earliest.estimate.projected_week_end is None
    assert earliest.estimate.no_estimate_reason == (
        "Needs at least 2 earlier published weeks back to back; only 1 was published before "
        "this week."
    )
    assert (earliest.estimate.effort_days, earliest.estimate.effort_is_override) == (2.0, False)

    # The Migration tab shows facts only; the slides keep the projection.
    migration = portal.get(f"/teams/{PACE_TEAM}/weeks/{W3.date()}/migration").text
    assert "working day" not in migration and "week of" not in migration
    slides = portal.get(f"/teams/{PACE_TEAM}/weeks/{W3.date()}/slides").text
    assert "week of 6 Sep 2026" in slides
    assert "registry" not in slides and "per day" not in slides


BASIS_TEAM = "basis-team"
_BASIS_ENTRY: dict[str, Any] = {
    "team_id": BASIS_TEAM,
    "display_name": "Basis Team",
    "v1_operators": ["basis", "BASIS"],
    "v2_operator": "basis-v2",
    "panels": [{"panel_id": "p1", "schema": "v1", "sql": "SELECT 1 WHERE node_name != 'x'"}],
}
_PANEL = _BASIS_ENTRY["panels"][0]
#: Back-to-back published weeks: (registry_version, ruleset_version, entry changes, whether
#: the week was measured differently from the one before it).
_BASIS_WEEKS: tuple[tuple[str, str, dict[str, Any], bool], ...] = (
    ("r1", "1.1.0", {}, False),
    # Another team was enrolled: the file's version moved, this team's entry did not.
    ("r2", "1.1.0", {}, False),
    ("r3", "1.1.0", {"display_name": "Basis", "weekly_review": {"enabled": True}}, False),
    ("r3", "1.1.0", {"planning": {"v1_rule_effort_days": 2}}, False),
    ("r4", "1.1.0", {"v1_operators": ["basis"]}, True),
    ("r5", "1.1.0", {"v1_operators": ["basis"]}, False),
    # Operators match case-sensitively, so a case change is a change despite the collation.
    ("r5", "1.1.0", {"v1_operators": ["Basis"]}, True),
    ("r5", "1.1.0", {"v1_operators": ["Basis"], "panels": [{**_PANEL, "sql": "SELECT 1"}]}, True),
    (
        "r5",
        "1.1.0",
        {
            "v1_operators": ["Basis"],
            "panels": [
                {
                    **_PANEL,
                    "sql": "SELECT 1",
                    "variables": [{"name": "n", "type": "constant", "value": "x"}],
                }
            ],
        },
        True,
    ),
    ("r6", "1.1.0", {"v1_operators": ["Basis"], "v2_operator": None, "panels": []}, True),
    ("r6", "1.1.0", {"v1_operators": ["Basis"], "v2_operator": "basis-v2 ", "panels": []}, True),
    ("r6", "1.2.0", {"v1_operators": ["Basis"], "v2_operator": "basis-v2 ", "panels": []}, True),
    ("r7", "1.2.0", {"v1_operators": ["Basis"], "v2_operator": "basis-v2 ", "panels": []}, False),
)


def test_the_view_and_the_operator_app_agree_on_when_measurement_changed(
    portal: TestClient,
) -> None:
    """portal_reviews.basis_changed (migration 008) and the admin app's Python mirror compare
    the same thing: the ruleset and the team's own operators and panels, never the registry
    file's version, which every enrolment bumps."""
    first_end = W3 - len(_BASIS_WEEKS) * WEEK
    ends = [first_end + index * WEEK for index in range(len(_BASIS_WEEKS))]
    with connect(CONFIG.sql, DB) as db:
        for index, (registry, ruleset, changes, _) in enumerate(_BASIS_WEEKS):
            name = f"basis{index}"
            RUN[name] = name.ljust(64, "3")
            snapshot = json.dumps(
                {**_BASIS_ENTRY, **changes}, separators=(",", ":"), ensure_ascii=False
            )
            persist_run(
                db,
                PersistencePayload(
                    run=sample_run(
                        run_id=_run_id(name),
                        team_id=BASIS_TEAM,
                        team_display_name="Basis Team",
                        run_at=ends[index],
                        window_start=ends[index] - WEEK,
                        window_end=ends[index],
                        registry_version=registry,
                        ruleset_version=ruleset,
                        registry_entry_snapshot=snapshot,
                    ),
                    daily_metrics=_daily(_run_id(name), BASIS_TEAM, ends[index]),
                    findings=[sample_finding(run_id=_run_id(name), key_field=f"{name}:k")],
                ),
            )
            publish_run(db, _run_id(name), published_by="operator")

        view = db.query(
            "SELECT basis_changed FROM portal_reviews WHERE team_id = :t ORDER BY window_end",
            {"t": BASIS_TEAM},
        )
        stored = db.query(
            "SELECT r.ruleset_version, r.registry_entry_snapshot FROM review_publications AS p "
            "JOIN runs AS r ON r.run_id = p.run_id WHERE p.team_id = :t "
            "AND p.withdrawn_at IS NULL ORDER BY p.window_end",
            {"t": BASIS_TEAM},
        )
        admin = _published_history(db, BASIS_TEAM, ends[-1])

    expected = [changed for *_, changed in _BASIS_WEEKS]
    assert [bool(row["basis_changed"]) for row in view] == expected
    assert basis_changes(stored) == expected
    assert [week.basis_changed for week in admin] == expected

    # The latest week's lookback stops at the ruleset change one week earlier, and the reason
    # says so rather than counting weeks.
    with connect(CONFIG.sql, DB) as db:
        reason = summarize(load_portal_summary(db, BASIS_TEAM, _run_id("basis12")))
    assert reason.estimate.no_estimate_reason is not None
    assert "measured the same way" in reason.estimate.no_estimate_reason
    assert "changed 1 week earlier" in reason.estimate.no_estimate_reason
    page = portal.get(f"/teams/{BASIS_TEAM}/weeks/{ends[-1].date()}/migration").text
    assert "registry" not in page and "ruleset" not in page


def _alert(portal: TestClient, schema: str, application: str, key: str, week: datetime = W3) -> str:
    response = portal.get(
        f"/teams/{TEAM}/weeks/{week.date()}/alert",
        params={"schema": schema, "application": application, "key": key},
    )
    assert response.status_code == 200, response.text
    return str(response.text)


def test_an_earlier_matching_row_is_labelled_apart_from_the_latest_event(
    portal: TestClient,
) -> None:
    page = _alert(portal, "v1", "checkout-svc", "checkout-svc:cart:node-1")
    assert "Matched event · stored sample" in page
    assert "error occurred" in page
    assert "Latest event" in page and "Cart error rate above 2% over 5m on node-1" in page
    assert "3 of 20 events matched." in page
    assert 'class="tab on" href="/teams/portal-team/weeks/2026-08-30/fix"' in page


def test_a_rule_finding_explains_itself_and_shows_its_decision_history(portal: TestClient) -> None:
    page = _alert(portal, "v1", "notif-dispatcher", "notif-dispatcher:dispatch-queue:notif-node-2")
    assert "something went wrong" in page and "592 of 592 events matched." in page
    assert "<b>Fix:</b>" in page
    assert page.index("Raised in the review meeting.") < page.index("Team agreed to rewrite it.")
    assert "Skipped: the problems above" in page
    assert "<dt>Runbook</dt>" not in page, "a v1 alert has no runbook field to show"


def test_a_model_finding_is_advisory_and_cites_its_principle(portal: TestClient) -> None:
    page = _alert(portal, "v1", "email-worker", "email-worker:template-renderer:email-node-3")
    assert '<span class="chip model">Advisory</span>' in page and "advisory" in page
    assert "States the outcome, not the failure" in page
    assert "high confidence" in page
    assert "Reports an exception but not which send path failed." in page


def test_needs_review_states_the_decision_a_person_must_make(portal: TestClient) -> None:
    page = _alert(portal, "v2", "push-gateway", "7e21c0d94ab35f68")
    assert "<b>Decide:</b>" in page and "medium confidence" in page
    assert "https://runbooks.internal/tokens" in page


def test_readiness_gaps_stay_apart_from_quality_and_untrusted_text_stays_inert(
    portal: TestClient,
) -> None:
    page = _alert(portal, "v2", "sms-gateway", "958e442f8ad32241")
    quality = page[page.index('class="colmain"') : page.index("Automated review")]
    assert "No problem found by the standard rules." in quality
    readiness = page[page.index("Get v2 ready</h2>") :]
    assert "blocks phase 2" in readiness
    assert "Runbook is being written." in readiness
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert 'href="javascript:' not in page


def test_a_decision_does_not_carry_to_the_new_key_minted_by_enrichment(portal: TestClient) -> None:
    page = _alert(portal, "v2", "sms-gateway", "41b8e07c2d9a6f13")
    assert "Runbook is being written." not in page
    assert "Users cannot sign in" in page


def test_an_unknown_alert_is_not_found(portal: TestClient) -> None:
    response = portal.get(
        f"/teams/{TEAM}/weeks/{W3.date()}/alert",
        params={"schema": "v1", "application": "nope", "key": "nope"},
    )
    assert response.status_code == 404


def test_every_page_carries_the_security_headers(portal: TestClient) -> None:
    week = f"/teams/{TEAM}/weeks/{W3.date()}"
    for path in ("/", f"/teams/{TEAM}", f"{week}/fix", f"{week}/history", "/healthz"):
        response = portal.get(path)
        assert response.status_code == 200, path
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"


def test_a_write_to_the_portal_never_reaches_the_database(portal: TestClient) -> None:
    before = _publication_count()
    assert portal.post("/runs", json={"team": TEAM}).status_code == 405
    assert portal.post(f"/teams/{TEAM}").status_code == 405
    assert _publication_count() == before


def _publication_count() -> int:
    with connect(CONFIG.sql, DB) as db:
        row = db.query_one("SELECT COUNT(*) AS n FROM review_publications")
    assert row is not None
    return int(row["n"])


# ------------------------------------------------------------------ the real pipeline


def test_portal_totals_equal_what_the_pipeline_stored_and_exported(reader: SqlConfig) -> None:
    """Run a mock team for real, publish it, and read it back as a reader would."""
    import csv
    import io
    from datetime import UTC

    from src.es.client import EsClient
    from src.es.reader import V1_INDEX
    from src.llm.fake import FakeLlmClient
    from src.report.render import build_run_outputs
    from src.run.orchestrator import execute_run

    es = EsClient(CONFIG.es)
    try:
        if not es.index_exists(V1_INDEX):
            pytest.skip("mock Elasticsearch has no appchi-v1 index")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"mock Elasticsearch is not reachable: {exc}")

    with connect(CONFIG.sql, DB) as db:
        payload, summary = execute_run(
            team_id="notifications-svc",
            run_at=datetime(2026, 8, 25, 18, 0, tzinfo=UTC),
            config=CONFIG,
            es_client=es,
            llm_client=FakeLlmClient(),
            llm_disabled_reason=None,
            registry_path=None,
            db=db,
        )
        persist_run(db, payload)
        publish_run(db, summary.run_id, published_by="operator")
        outputs = build_run_outputs(db, summary.run_id)
        stored_events = {
            str(row["alert_schema"]): int(row["events"])
            for row in db.query(
                "SELECT alert_schema, SUM(alerts) AS events FROM daily_metrics "
                "WHERE run_id = :r GROUP BY alert_schema",
                {"r": summary.run_id},
            )
        }

    worklist = list(csv.DictReader(io.StringIO(outputs["alert_worklist.csv"])))
    exported_distinct = {
        schema: sum(1 for row in worklist if row["schema"] == schema) for schema in ("v1", "v2")
    }
    assert exported_distinct == {"v1": summary.v1_identities, "v2": summary.v2_identities}
    assert stored_events == {"v1": summary.v1_rows, "v2": summary.v2_rows}

    with connect(reader, DB) as db:
        totals = {
            str(row["alert_schema"]): (int(row["events"]), int(row["distinct_alerts"]))
            for row in db.query(
                "SELECT alert_schema, events, distinct_alerts FROM portal_schema_totals "
                "WHERE run_id = :r",
                {"r": summary.run_id},
            )
        }
    assert totals == {
        schema: (stored_events[schema], exported_distinct[schema]) for schema in ("v1", "v2")
    }

    settings = PortalSettings(sql=reader, database=DB, page_size=50)
    with TestClient(build_portal(settings), client=LOCAL) as client:
        page = client.get("/teams/notifications-svc").text
    assert _cards(page) == [
        (summary.v1_identities, summary.v1_rows),
        (summary.v2_identities, summary.v2_rows),
    ]
    assert summary.run_id not in page
