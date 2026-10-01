"""The operator admin app, against a disposable SQL Server database (design section 7.12)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from src.admin.app import build_admin
from src.config import load_config
from src.config.admin import AdminSettings
from src.db.connection import connect
from src.db.migrate import reset_test_database
from src.db.repositories import PersistencePayload, persist_run

from tests.helpers.sql import sample_daily, sample_finding, sample_run

pytestmark = pytest.mark.integration

CONFIG = load_config()
DB = CONFIG.sql.test_database
TEAM = "checkout-api"
WEEK = timedelta(hours=168)
W2 = datetime(2026, 8, 24)
W1 = W2 - WEEK
RUN1, RUN2 = "1" * 64, "2" * 64
ALICE = {"X-Forwarded-User": "alice"}
SECRET = "s" * 40


def _store(run_id: str, end: datetime) -> None:
    payload = PersistencePayload(
        run=sample_run(run_id=run_id, run_at=end, window_start=end - WEEK, window_end=end),
        daily_metrics=[sample_daily(run_id=run_id)],
        findings=[
            sample_finding(
                run_id=run_id,
                message="Something went wrong",
                core_rule_ids="R1",
                quality_state="rule_flagged",
                llm_principle_id=None,
                llm_confidence=None,
                llm_justification=None,
                findings_evidence='[{"rule_id":"R1","matched_rows":4,"sample_evidence":'
                '{"field":"message","normalized":"something went wrong"}}]',
            )
        ],
    )
    with connect(CONFIG.sql, DB) as db:
        persist_run(db, payload)


#: A third, never-published week of the same team with a mixed work list, and a run of
#: another team, for the Summary page.
RUN0, OTHER, FAILED = "0" * 64, "9" * 64, "f" * 64
W0 = W1 - WEEK


def _store_summary_runs() -> None:
    def alert(key: str, **overrides: object) -> dict[str, object]:
        return sample_finding(run_id=RUN0, key_field=key, **overrides)

    payload = PersistencePayload(
        run=sample_run(
            run_id=RUN0,
            run_at=W0,
            window_start=W0 - WEEK,
            window_end=W0,
            registry_entry_snapshot=(
                '{"team_id":"checkout-api","v1_operators":["checkout"],"v2_operator":"co-v2",'
                '"panels":[{"panel_id":"main","schema":"v1",'
                '"sql":"SELECT * FROM alerts WHERE node_name != \'junk\' AND a < 3"}]}'
            ),
        ),
        daily_metrics=[
            sample_daily(run_id=RUN0),
            sample_daily(run_id=RUN0, alert_schema="v2", alerts=2, distinct_alerts=1),
        ],
        findings=[
            alert(
                "k-r1",
                message="Something went wrong <b>now</b>",
                core_rule_ids="R1",
                quality_state="rule_flagged",
                llm_principle_id=None,
                llm_confidence=None,
                llm_justification=None,
            ),
            alert(
                "k-r10",
                message="Disk full on R10 host",
                core_rule_ids="R10",
                quality_state="rule_flagged",
                llm_principle_id=None,
                llm_confidence=None,
                llm_justification=None,
            ),
            alert("k-good", message="Checkout latency above 2s"),
            alert(
                "k-v2",
                alert_schema="v2",
                message="Payment errors above 1%",
                readiness_rule_ids="R8",
            ),
        ],
    )
    other = PersistencePayload(
        run=sample_run(run_id=OTHER, team_id="payments-api", team_display_name="Payments API"),
        daily_metrics=[sample_daily(run_id=OTHER, team_id="payments-api")],
        findings=[sample_finding(run_id=OTHER)],
    )
    # The newest run of the team, but it did not complete: never a Summary.
    failed = PersistencePayload(
        run=sample_run(
            run_id=FAILED,
            run_at=W2 + WEEK,
            window_start=W2,
            window_end=W2 + WEEK,
            status="failed",
            completed_at=None,
            error_summary="Elasticsearch unreachable",
        ),
    )
    with connect(CONFIG.sql, DB) as db:
        persist_run(db, payload)
        persist_run(db, other)
        persist_run(db, failed)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    try:
        reset_test_database(CONFIG.sql, DB)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"SQL Server is not reachable: {exc}")
    _store(RUN1, W1)
    _store(RUN2, W2)
    _store_summary_runs()
    app = build_admin(AdminSettings(config=CONFIG, database=DB, secret=SECRET))
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def token(client: TestClient, headers: dict[str, str] = ALICE) -> str:
    page = client.get(f"/teams/{TEAM}", headers=headers).text
    found = re.search(r'name="csrf" value="([0-9a-f]+)"', page)
    assert found, "the team page carries a form token"
    return found.group(1)


def publications() -> list[dict[str, object]]:
    with connect(CONFIG.sql, DB) as db:
        return db.query(
            "SELECT run_id, published_by, withdrawn_by, withdrawn_reason FROM review_publications "
            "ORDER BY publication_id"
        )


# ------------------------------------------------------------------ who may do what


def test_nobody_gets_in_without_the_login_proxys_identity(client: TestClient) -> None:
    assert client.get("/").status_code == 401
    assert client.post(f"/runs/{RUN1}/publish").status_code == 401


def test_the_signed_in_operator_sees_every_team_and_run(client: TestClient) -> None:
    home = client.get("/", headers=ALICE)
    assert home.status_code == 200 and "Signed in as alice" in home.text
    assert "Checkout API" in home.text
    team = client.get(f"/teams/{TEAM}", headers=ALICE).text
    assert RUN1[:16] in team and RUN2[:16] in team and "not published" in team


def test_a_write_without_a_valid_token_is_refused(client: TestClient) -> None:
    assert client.post(f"/runs/{RUN1}/publish", headers=ALICE, data={}).status_code == 403
    assert (
        client.post(f"/runs/{RUN1}/publish", headers=ALICE, data={"csrf": "0" * 64}).status_code
        == 403
    )
    assert publications() == []


def test_another_operators_token_does_not_work(client: TestClient) -> None:
    bobs = token(client, {"X-Forwarded-User": "bob"})
    response = client.post(f"/runs/{RUN1}/publish", headers=ALICE, data={"csrf": bobs})
    assert response.status_code == 403


def test_a_cross_site_form_is_refused_even_with_a_token(client: TestClient) -> None:
    response = client.post(
        f"/runs/{RUN1}/publish",
        headers={**ALICE, "Sec-Fetch-Site": "cross-site"},
        data={"csrf": token(client)},
    )
    assert response.status_code == 403


def test_other_methods_are_refused(client: TestClient) -> None:
    assert client.put("/", headers=ALICE).status_code == 405
    assert client.delete(f"/runs/{RUN1}/publish", headers=ALICE).status_code == 405


# ------------------------------------------------------------------ the operator's actions


def test_publishing_withdrawing_and_deciding_are_recorded_under_the_operator(
    client: TestClient,
) -> None:
    csrf = token(client)
    first = client.post(
        f"/runs/{RUN1}/publish", headers=ALICE, data={"csrf": csrf, "note": "First week"}
    )
    assert first.status_code == 303 and "Published" in first.headers["location"]
    second = client.post(f"/runs/{RUN2}/publish", headers=ALICE, data={"csrf": csrf})
    assert second.status_code == 303
    assert [row["published_by"] for row in publications()] == ["alice", "alice"]

    refused = client.post(f"/runs/{RUN2}/publish", headers=ALICE, data={"csrf": csrf})
    assert "error=" in refused.headers["location"], "a refusal is shown, not raised"

    decided = client.post(
        f"/runs/{RUN2}/decide",
        headers=ALICE,
        data={
            "csrf": csrf,
            "schema": "v1",
            "application": "checkout-api",
            "key_field": "checkout-api:cart:node-1",
            "finding": "R1",
            "state": "confirmed",
            "note": "Agreed with the team",
        },
    )
    assert decided.status_code == 303 and "done=" in decided.headers["location"]
    wrong = client.post(
        f"/runs/{RUN2}/decide",
        headers=ALICE,
        data={
            "csrf": csrf,
            "schema": "v1",
            "application": "checkout-api",
            "key_field": "checkout-api:cart:node-1",
            "finding": "R2",
            "state": "confirmed",
            "note": "x",
        },
    )
    assert "error=" in wrong.headers["location"]
    with connect(CONFIG.sql, DB) as db:
        decisions = db.query("SELECT finding_id, decided_by FROM finding_decisions")
    assert decisions == [{"finding_id": "R1", "decided_by": "alice"}]
    findings = client.get(f"/runs/{RUN2}/findings", headers=ALICE).text
    assert "Agreed with the team" in findings

    withdrawn = client.post(
        f"/runs/{RUN2}/withdraw", headers=ALICE, data={"csrf": csrf, "reason": "Wrong week"}
    )
    assert withdrawn.status_code == 303
    last = publications()[-1]
    assert last["withdrawn_by"] == "alice" and last["withdrawn_reason"] == "Wrong week"


def test_the_full_scorecard_renders_from_sql_with_its_own_policy(client: TestClient) -> None:
    response = client.get(f"/runs/{RUN1}/scorecard", headers=ALICE)
    assert response.status_code == 200
    assert "<html" in response.text.lower()
    assert "style-src 'unsafe-inline'" in response.headers["content-security-policy"]
    assert "script-src" not in response.headers["content-security-policy"]


def test_an_unknown_run_is_not_found(client: TestClient) -> None:
    assert client.get("/runs/nope/scorecard", headers=ALICE).status_code == 404


# ------------------------------------------------------------------ the Summary page


def test_the_summary_needs_the_login_proxys_identity(client: TestClient) -> None:
    response = client.get(f"/teams/{TEAM}/summary")
    assert response.status_code == 401
    assert "content-security-policy" in response.headers


def test_the_summary_of_a_persisted_run_renders_for_the_signed_in_operator(
    client: TestClient,
) -> None:
    response = client.get(f"/teams/{TEAM}/summary?run_id={RUN0}", headers=ALICE)
    assert response.status_code == 200
    page = response.text
    assert "Signed in as alice" in page
    assert "script-src" not in response.headers["content-security-policy"]
    assert "<script" not in page and " style=" not in page
    assert "Something went wrong &lt;b&gt;now&lt;/b&gt;" in page
    assert RUN0 in page and "never published" in page
    assert "<mark" in page and "a &lt; 3" in page, "panel SQL is escaped and marked"
    for run_id in (RUN0, RUN1, RUN2):
        assert f'<option value="{run_id}"' in page, "the run picker lists the team's runs"
    assert f'<option value="{OTHER}"' not in page


def test_the_summary_defaults_to_the_latest_completed_run(client: TestClient) -> None:
    page = client.get(f"/teams/{TEAM}/summary", headers=ALICE).text
    assert f'<option value="{RUN2}" selected>' in page


def test_the_summary_of_a_published_week_reads_its_published_history(client: TestClient) -> None:
    with connect(CONFIG.sql, DB) as db:
        published = db.query_one(
            "SELECT run_id FROM review_publications WHERE withdrawn_at IS NULL "
            "AND team_id = :team ORDER BY window_end DESC",
            {"team": TEAM},
        )
    assert published is not None, "the publishing test above left a week published"
    page = client.get(f"/teams/{TEAM}/summary?run_id={published['run_id']}", headers=ALICE)
    assert page.status_code == 200 and "published</span>" in page.text
    assert "This week is not published" not in page.text
    assert "found 0" in page.text, "one published week: no earlier week to measure a pace from"


def test_the_admin_summary_renders_the_shared_widgets(client: TestClient) -> None:
    page = client.get(f"/teams/{TEAM}/summary?run_id={RUN0}", headers=ALICE).text
    for title in ("Why alerts were flagged", "How often alerts fire", "Migration progress"):
        assert title in page
    assert "per day" in page, "the admin surface shows per-day rates"
    assert f"/teams/{TEAM}/summary?run_id={RUN0}&amp;rule=R1#worklist" in page
    assert "This week is not published" in page, "the estimate reads published weeks only"
    assert "<script" not in page and " style=" not in page


def test_the_admin_summary_ends_with_two_presentation_slides(client: TestClient) -> None:
    page = client.get(f"/teams/{TEAM}/summary?run_id={RUN0}", headers=ALICE).text
    slides = page[page.index(">Presentation</h3>") : page.index('id="days"')]
    assert slides.count('<section class="slide ') == 2
    assert "stands</h4>" in slides and "The week, day by day</h4>" in slides
    assert "Biggest single alert" in slides and "What to fix" not in slides
    assert "Top rules" not in slides and "<polyline" not in slides
    # Both fixtures store non-zero day buckets for v1 and v2: two charts, no empty box.
    assert slides.count('<svg class="sl-chart"') == 2
    assert "No v1 alerts this week" not in slides
    assert "No v2 alerts this week" not in slides
    assert RUN0 not in slides, "the slides carry no run id on either surface"


def test_the_work_list_sorts_by_events_or_application(client: TestClient) -> None:
    base = f"/teams/{TEAM}/summary?run_id={RUN0}&schema=v1"
    by_app = client.get(base + "&sort=application", headers=ALICE).text
    assert '<option value="application" selected>' in by_app
    assert client.get(base + "&sort=bogus", headers=ALICE).status_code == 422


def test_an_unknown_run_or_another_teams_run_is_not_found(client: TestClient) -> None:
    assert client.get(f"/teams/{TEAM}/summary?run_id=nope", headers=ALICE).status_code == 404
    assert client.get(f"/teams/{TEAM}/summary?run_id={OTHER}", headers=ALICE).status_code == 404
    assert client.get("/teams/no-such-team/summary", headers=ALICE).status_code == 404


def test_a_run_that_did_not_complete_is_not_found(client: TestClient) -> None:
    assert client.get(f"/teams/{TEAM}/summary?run_id={FAILED}", headers=ALICE).status_code == 404
    latest = client.get(f"/teams/{TEAM}/summary", headers=ALICE).text
    assert f'value="{FAILED}"' not in latest, "the picker offers completed runs only"


def test_the_work_list_filters_by_state_schema_and_rule(client: TestClient) -> None:
    base = f"/teams/{TEAM}/summary?run_id={RUN0}"
    everything = client.get(base, headers=ALICE).text
    assert "Showing 1&ndash;4 of 4" in everything
    flagged = client.get(base + "&state=rule_flagged&rule=R1", headers=ALICE).text
    assert "Showing 1&ndash;1 of 1" in flagged
    worklist = flagged[flagged.index('id="worklist"') :]
    assert "Something went wrong" in worklist and "Disk full" not in worklist
    v2 = client.get(base + "&schema=v2", headers=ALICE).text
    assert "Showing 1&ndash;1 of 1" in v2 and "Payment errors" in v2
    assert "/findings" in v2, "each row links to the findings and decisions page"
    for bad in ("&state=bogus", "&schema=v3", "&rule=R11", "&rule=P1"):
        assert client.get(base + bad, headers=ALICE).status_code == 422


def test_the_summary_is_read_only(client: TestClient) -> None:
    assert client.post(f"/teams/{TEAM}/summary", headers=ALICE).status_code == 405
    assert client.put(f"/teams/{TEAM}/summary", headers=ALICE).status_code == 405


def test_the_team_page_and_dashboard_link_to_the_summary(client: TestClient) -> None:
    assert f"/teams/{TEAM}/summary" in client.get("/", headers=ALICE).text
    team = client.get(f"/teams/{TEAM}", headers=ALICE).text
    assert f"/teams/{TEAM}/summary?run_id={RUN0}" in team
