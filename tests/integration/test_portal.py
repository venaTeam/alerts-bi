"""The review portal, against a real disposable SQL Server database (design section 7.10).

Synthetic runs are persisted through the same ``persist_run`` the pipeline uses, published
through the operator functions, and read back by the portal over its own read-only login -
never the owning credential. One test drives the real pipeline over the mock Elasticsearch
data, so the portal's totals are checked against numbers the pipeline itself stored.
"""

from __future__ import annotations

import dataclasses
import json
import secrets
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from src.config import PortalSettings, SqlConfig, load_config
from src.db.connection import connect
from src.db.migrate import reset_test_database
from src.db.reader import grant_reader, read_only_problems
from src.db.repositories import PersistencePayload, RunIsPublished, persist_run
from src.portal.app import build_portal
from src.portal.explain import EN_DASH
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
        findings=_findings(run_id)
        if rich
        else [sample_finding(run_id=run_id, key_field=f"{name}:only", row_count=5)],
    )
    with connect(CONFIG.sql, DB) as db:
        persist_run(db, payload)


@pytest.fixture(scope="module")
def reader() -> SqlConfig:
    """A fresh database with three published weeks, and the portal's own read-only login."""
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
    settings = PortalSettings(sql=reader, database=DB, page_size=3)
    with TestClient(build_portal(settings), client=LOCAL) as client:
        yield client


# ------------------------------------------------------------------ the credential


def test_the_reader_login_is_fit_for_the_portal(reader: SqlConfig) -> None:
    with connect(reader, DB) as db:
        assert read_only_problems(db) == []


def test_the_owning_credential_is_refused_as_a_portal_login(reader: SqlConfig) -> None:
    with connect(CONFIG.sql, DB) as db:
        assert read_only_problems(db), "sa can write; the portal must refuse it"


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
    assert "1,114</span>" in page and "13</span>" in page
    assert "distinct alerts this week" in page
    assert "per day" not in page
    assert "1,127" not in page, "v1 and v2 events are never added together"


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


def test_history_has_one_point_per_published_week(portal: TestClient) -> None:
    page = portal.get(f"/teams/{TEAM}").text
    # Two schemas x two measures, three contiguous weeks each.
    assert page.count("<polyline") == 4
    assert page.count('class="dot v1') == 6 and page.count('class="dot v2') == 6


def test_the_work_list_is_ordered_and_paginated_in_sql(portal: TestClient) -> None:
    first = portal.get(f"/teams/{TEAM}").text
    assert "Needs attention · 5" in first and "All alerts · 8" in first
    assert "Showing 1&ndash;3 of 5" in first
    order = [
        first.index(text)
        for text in ("Something went wrong", "Cart error rate", "Unhandled exception")
    ]
    assert order == sorted(order), "rule findings by event count, then model findings"

    second = portal.get(f"/teams/{TEAM}", params={"page": 2}).text
    assert "Showing 4&ndash;5 of 5" in second
    assert "Nightly token cleanup" in second and "Something went wrong" not in second

    everything = portal.get(
        f"/teams/{TEAM}", params={"show": "all", "schema": "v2", "page": 2}
    ).text
    assert "Showing 4&ndash;4 of 4" in everything


def _alert(portal: TestClient, schema: str, application: str, key: str, week: datetime = W3) -> str:
    response = portal.get(
        f"/teams/{TEAM}/weeks/{week.date()}/alert",
        params={"schema": schema, "application": application, "key": key},
    )
    assert response.status_code == 200, response.text
    return str(response.text)


def test_an_earlier_matching_row_is_labelled_apart_from_the_latest_firing(
    portal: TestClient,
) -> None:
    page = _alert(portal, "v1", "checkout-svc", "checkout-svc:cart:node-1")
    assert "Matching firing · stored sample" in page
    assert "error occurred" in page
    assert "Latest firing" in page and "Cart error rate above 2% over 5m on node-1" in page
    assert "3 of 20 firings this week matched." in page


def test_a_rule_finding_explains_itself_and_shows_its_decision_history(portal: TestClient) -> None:
    page = _alert(portal, "v1", "notif-dispatcher", "notif-dispatcher:dispatch-queue:notif-node-2")
    assert "&quot;something went wrong&quot;" in page and "592 of 592 firings" in page
    assert "Next step:" in page
    assert page.index("Raised in the review meeting.") < page.index("Team agreed to rewrite it.")
    assert "Skipped: the rule findings above" in page
    assert "not part of the v1 schema" in page


def test_a_model_finding_is_advisory_and_cites_its_principle(portal: TestClient) -> None:
    page = _alert(portal, "v1", "email-worker", "email-worker:template-renderer:email-node-3")
    assert "Automated finding · advisory" in page
    assert "States the outcome, not the failure" in page
    assert "Confidence: <b>high</b>" in page
    assert "Reports an exception but not which send path failed." in page


def test_needs_review_states_the_decision_a_person_must_make(portal: TestClient) -> None:
    page = _alert(portal, "v2", "push-gateway", "7e21c0d94ab35f68")
    assert "Decision needed:" in page and "Confidence: <b>medium</b>" in page
    assert "https://runbooks.internal/tokens" in page


def test_readiness_gaps_stay_apart_from_quality_and_untrusted_text_stays_inert(
    portal: TestClient,
) -> None:
    page = _alert(portal, "v2", "sms-gateway", "958e442f8ad32241")
    quality = page[page.index("Quality findings") : page.index("Automated review")]
    assert "No rule matched" in quality
    readiness = page[page.index("v2 readiness") :]
    assert "blocks phase 2" in readiness.lower() or "blocks phase-2" in readiness
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
    for path in ("/", f"/teams/{TEAM}", "/healthz"):
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
    assert f'<span class="n">{summary.v1_identities:,}</span>' in page
    assert f'<span class="n">{summary.v1_rows:,}</span>' in page
    assert f'<span class="n">{summary.v2_identities:,}</span>' in page
    assert summary.run_id not in page
