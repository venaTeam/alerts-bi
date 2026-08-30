"""The HTTP trigger surface, against the real mock Elasticsearch and a disposable database.

Integration rather than unit tests on purpose: the surface's whole job is to reach the same
pipeline the CLI reaches, so a test that stubbed the pipeline out would assert only that the
router works.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from alerts_bi.api import AlertsBiServer, build_server
from alerts_bi.config import load_config
from alerts_bi.db.migrate import reset_test_database
from alerts_bi.es.client import EsClient
from alerts_bi.es.reader import V1_INDEX
from alerts_bi.report.render import OUTPUT_FILES

pytestmark = pytest.mark.integration

CONFIG = load_config()

#: The mock dataset is generated against this fixed clock.
RUN_AT = "2026-08-25T18:00:00Z"
TEAM = "checkout-api"


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.text)


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[AlertsBiServer]:
    try:
        if not EsClient(CONFIG.es).index_exists(V1_INDEX):
            pytest.skip("mock Elasticsearch has no appchi-v1 index")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"mock Elasticsearch is not reachable: {exc}")
    try:
        reset_test_database(CONFIG.sql, CONFIG.sql.test_database)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"SQL Server is not reachable: {exc}")

    # Port 0 lets the OS pick a free port, so the suite never collides with a real server.
    instance = build_server(
        CONFIG,
        host="127.0.0.1",
        port=0,
        database=CONFIG.sql.test_database,
        out_root=Path(tmp_path_factory.mktemp("api-out")),
    )
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=5)


def base_url(server: AlertsBiServer) -> str:
    """The bound address. The host may come back as bytes for some address families."""
    host, port = server.server_address[:2]
    text = host.decode() if isinstance(host, bytes | bytearray) else str(host)
    return f"http://{text}:{port}"


def call(
    server: AlertsBiServer,
    path: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    # Loopback and a fixed scheme: the URL is built here, never taken from input.
    request = urllib.request.Request(
        f"{base_url(server)}{path}", data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request) as response:
            return Response(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers), exc.read())


# ------------------------------------------------------------------ discovery


def test_the_index_lists_every_registered_team_and_offers_a_run_form(
    server: AlertsBiServer,
) -> None:
    response = call(server, "/")
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert 'action="/runs"' in response.text
    assert TEAM in response.text


def test_the_team_list_comes_from_the_registry(server: AlertsBiServer) -> None:
    payload = call(server, "/teams", headers={"Accept": "application/json"}).json()
    teams = {team["team_id"]: team for team in payload["teams"]}
    assert TEAM in teams
    assert teams[TEAM]["display_name"] == "Checkout API"
    assert teams[TEAM]["v1_operators"] == ["checkout", "Checkout-API"]


def test_health_reports_each_dependency_separately(server: AlertsBiServer) -> None:
    payload = call(server, "/healthz").json()
    assert payload["checks"]["elasticsearch"]["ok"] is True
    assert payload["checks"]["sql_server"]["ok"] is True
    assert payload["ok"] is True


def test_an_unknown_route_is_a_404_rather_than_a_traceback(server: AlertsBiServer) -> None:
    response = call(server, "/nope")
    assert response.status == 404
    assert "Traceback" not in response.text


# ----------------------------------------------------------------------- runs


def test_a_run_returns_the_scorecard_html_for_that_run(server: AlertsBiServer) -> None:
    response = call(
        server,
        f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake",
        method="POST",
    )
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/html")
    assert "Alerts BI scorecard" in response.text
    assert "Checkout API" in response.text
    # The run identity travels in a header so a caller can fetch the CSVs afterwards.
    assert len(response.headers["X-Alerts-BI-Run-Id"]) == 64


def test_a_run_writes_the_four_approved_files_exactly_as_the_cli_does(
    server: AlertsBiServer,
) -> None:
    response = call(server, f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake", method="POST")
    out_dir = Path(response.headers["X-Alerts-BI-Out-Dir"])
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(OUTPUT_FILES)


def test_a_json_caller_gets_the_summary_and_links_instead_of_html(
    server: AlertsBiServer,
) -> None:
    payload = call(
        server,
        f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake",
        method="POST",
        headers={"Accept": "application/json"},
    ).json()
    assert payload["team_id"] == TEAM
    assert payload["phase"] == "phase_1"
    assert payload["v1"] == {"rows": 875, "distinct": 5}
    assert payload["llm_assessed"] is True
    assert payload["scorecard"] == f"/runs/{payload['run_id']}"
    assert set(payload["files"]) == set(OUTPUT_FILES)


def test_a_form_post_carries_the_same_parameters_as_the_query_string(
    server: AlertsBiServer,
) -> None:
    response = call(
        server,
        "/runs",
        method="POST",
        data=f"team={TEAM}&run_at={RUN_AT}&llm=fake".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status == 200
    assert "Alerts BI scorecard" in response.text


def test_a_json_body_carries_the_same_parameters_too(server: AlertsBiServer) -> None:
    response = call(
        server,
        "/runs",
        method="POST",
        data=json.dumps({"team": TEAM, "run_at": RUN_AT, "llm": "fake"}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    assert response.status == 200
    assert response.json()["team_id"] == TEAM


def test_the_same_team_and_clock_reuse_one_run_id(server: AlertsBiServer) -> None:
    first = call(server, f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake", method="POST")
    second = call(server, f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake", method="POST")
    assert first.headers["X-Alerts-BI-Run-Id"] == second.headers["X-Alerts-BI-Run-Id"]


# ------------------------------------------------------------- run refusals


def test_a_run_without_a_team_is_refused_rather_than_fanning_out(
    server: AlertsBiServer,
) -> None:
    response = call(server, "/runs", method="POST", headers={"Accept": "application/json"})
    assert response.status == 400
    assert "never defaults" in response.json()["error"]


def test_an_unknown_team_is_rejected_before_any_elasticsearch_query(
    server: AlertsBiServer,
) -> None:
    response = call(
        server, "/runs?team=not-a-team", method="POST", headers={"Accept": "application/json"}
    )
    assert response.status == 400
    assert "registry" in response.json()["error"]


def test_a_malformed_run_at_is_rejected(server: AlertsBiServer) -> None:
    response = call(
        server,
        f"/runs?team={TEAM}&run_at=last-tuesday",
        method="POST",
        headers={"Accept": "application/json"},
    )
    assert response.status == 400
    assert "ISO 8601" in response.json()["error"]


def test_an_unknown_llm_mode_is_rejected_rather_than_silently_defaulting(
    server: AlertsBiServer,
) -> None:
    response = call(
        server,
        f"/runs?team={TEAM}&llm=maybe",
        method="POST",
        headers={"Accept": "application/json"},
    )
    assert response.status == 400
    assert "llm must be one of" in response.json()["error"]


def test_a_second_concurrent_run_is_refused_rather_than_racing(
    server: AlertsBiServer,
) -> None:
    """Two runs of the same team and clock would derive one run_id and fight over its rows."""
    server.run_lock.acquire()
    try:
        response = call(
            server,
            f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake",
            method="POST",
            headers={"Accept": "application/json"},
        )
    finally:
        server.run_lock.release()
    assert response.status == 409
    assert "already in progress" in response.json()["error"]


# -------------------------------------------------------------------- reports


def test_a_stored_run_renders_from_sql_on_demand(server: AlertsBiServer) -> None:
    run_id = call(server, f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake", method="POST").headers[
        "X-Alerts-BI-Run-Id"
    ]

    response = call(server, f"/runs/{run_id}")
    assert response.status == 200
    assert "Alerts BI scorecard" in response.text


@pytest.mark.parametrize("name", ["daily_metrics.csv", "rule_counts.csv", "alert_worklist.csv"])
def test_each_csv_export_is_served_from_the_stored_rows(server: AlertsBiServer, name: str) -> None:
    run_id = call(server, f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake", method="POST").headers[
        "X-Alerts-BI-Run-Id"
    ]

    response = call(server, f"/runs/{run_id}/{name}")
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/csv")
    assert name in response.headers["Content-Disposition"]
    # RFC 4180 line endings survive the transport.
    assert response.body.count(b"\r\n") > 1


def test_latest_resolves_that_team_s_most_recent_run(server: AlertsBiServer) -> None:
    expected = call(server, f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake", method="POST").headers[
        "X-Alerts-BI-Run-Id"
    ]

    response = call(server, f"/runs/latest?team={TEAM}")
    assert response.status == 200
    assert response.headers["X-Alerts-BI-Run-Id"] == expected


def test_latest_without_a_team_is_refused(server: AlertsBiServer) -> None:
    response = call(server, "/runs/latest", headers={"Accept": "application/json"})
    assert response.status == 400
    assert "team" in response.json()["error"]


def test_an_unknown_run_id_is_a_404_not_an_empty_report(server: AlertsBiServer) -> None:
    response = call(server, f"/runs/{'f' * 64}", headers={"Accept": "application/json"})
    assert response.status == 404
    assert "not in the store" in response.json()["error"]


def test_only_the_four_approved_outputs_are_reachable(server: AlertsBiServer) -> None:
    """The file contract is the file contract; the surface serves nothing else."""
    run_id = call(server, f"/runs?team={TEAM}&run_at={RUN_AT}&llm=fake", method="POST").headers[
        "X-Alerts-BI-Run-Id"
    ]

    for name in ("../../.env", "findings.json", "runs.csv"):
        response = call(server, f"/runs/{run_id}/{name}", headers={"Accept": "application/json"})
        assert response.status == 404, name
