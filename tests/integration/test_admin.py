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


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    try:
        reset_test_database(CONFIG.sql, DB)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"SQL Server is not reachable: {exc}")
    _store(RUN1, W1)
    _store(RUN2, W2)
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
