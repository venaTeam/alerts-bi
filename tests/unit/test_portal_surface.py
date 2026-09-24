"""The reader portal's guarantees that need no database (design section 7.10).

GET only; no import path to the run pipeline, the run endpoint, Elasticsearch, the model or
an operator write; only allowlisted clients; no script and no inline style; alert text
escaped; only absolute http(s) URLs become links.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from src.config import load_config
from src.config.portal import PortalSettings, parse_networks
from src.portal.app import SECURITY_HEADERS, build_portal, client_allowed
from src.portal.charts import ChartPoint, line_chart
from src.portal.pages import alert_page, safe_link, team_page
from src.portal.queries import AlertDetail, AlertRow, Decision, Review, SchemaTotals, WorklistPage

PORTAL_DIR = Path(__file__).resolve().parents[2] / "src" / "portal"
SETTINGS = PortalSettings(sql=load_config().sql, database="alerts_bi_test")

#: Packages a reader's request must have no path to.
FORBIDDEN = ("src.api", "src.run", "src.es", "src.llm", "src.review", "src.report", "src.cli")


# ------------------------------------------------------------------ isolation


def test_every_route_is_a_read() -> None:
    app = build_portal(SETTINGS)
    for route in app.routes:
        if isinstance(route, APIRoute):
            assert route.methods is not None
            assert route.methods <= {"GET", "HEAD"}, f"{route.path} accepts {route.methods}"


@pytest.mark.parametrize("module", sorted(PORTAL_DIR.glob("*.py")), ids=lambda p: p.name)
def test_the_portal_imports_nothing_that_could_run_or_write(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    for name in imported:
        assert not name.startswith(FORBIDDEN), f"{module.name} imports {name}"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/", "/runs", "/teams/x", "/teams/x/weeks/2026-08-30"])
def test_any_write_method_is_refused_before_routing(method: str, path: str) -> None:
    with TestClient(build_portal(SETTINGS), client=("10.1.2.3", 50000)) as client:
        response = client.request(method, path)
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"


def test_there_is_no_run_endpoint_and_no_api_docs() -> None:
    paths = {route.path for route in build_portal(SETTINGS).routes if isinstance(route, APIRoute)}
    assert not any(path.startswith("/runs") for path in paths)
    assert "/docs" not in paths and "/openapi.json" not in paths


# ------------------------------------------------------------------ network


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("127.0.0.1", True),
        ("10.20.30.40", True),
        ("172.16.5.5", True),
        ("192.168.1.1", True),
        ("::1", True),
        ("8.8.8.8", False),
        ("172.32.0.1", False),
        ("testclient", False),
        (None, False),
    ],
)
def test_only_company_network_clients_are_admitted(host: str | None, allowed: bool) -> None:
    assert client_allowed(host, SETTINGS.networks()) is allowed


def test_an_outside_client_is_refused_with_403() -> None:
    with TestClient(build_portal(SETTINGS), client=("8.8.8.8", 50000)) as client:
        assert client.get("/").status_code == 403


def test_an_empty_or_malformed_allowlist_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_networks(())
    with pytest.raises(ValueError, match="not a network"):
        parse_networks(("office",))


def test_the_security_headers_forbid_script_and_inline_style() -> None:
    policy = SECURITY_HEADERS["Content-Security-Policy"]
    assert "default-src 'none'" in policy
    assert "unsafe-inline" not in policy and "script-src" not in policy
    with TestClient(build_portal(SETTINGS), client=("8.8.8.8", 50000)) as client:
        refused = client.get("/")
    assert refused.headers["content-security-policy"] == policy


# ------------------------------------------------------------------ links and escaping


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://runbooks.internal/x", "https://runbooks.internal/x"),
        ("http://grafana.internal/d/1", "http://grafana.internal/d/1"),
        ("javascript:alert(1)", None),
        ("JAVASCRIPT:alert(1)", None),
        ("data:text/html,<b>x</b>", None),
        ("//evil.example/x", None),
        ("/relative/path", None),
        ("https://", None),
        ("https://host/with space", None),
        ("", None),
        (None, None),
        (5, None),
    ],
)
def test_only_absolute_http_urls_become_links(url: object, expected: str | None) -> None:
    assert safe_link(url) == expected


END = datetime(2026, 8, 30, 16, 44, 35)
HOSTILE = '<script>alert("x")</script>'


def _review(end: datetime = END, run_id: str = "r3") -> Review:
    return Review(
        team_id="team<x>",
        team_name=f"Team {HOSTILE}",
        run_id=run_id,
        window_start=end - timedelta(hours=168),
        window_end=end,
        published_at=end + timedelta(hours=16),
        review_note=f"note {HOSTILE}",
        phase="phase_1",
        readiness_pct=20.0,
        totals={
            "v1": SchemaTotals(events=925, distinct_alerts=6),
            "v2": SchemaTotals(events=16, distinct_alerts=5),
        },
    )


def _alert(**overrides: object) -> AlertRow:
    values: dict[str, object] = {
        "alert_schema": "v2",
        "application": f"app {HOSTILE}",
        "key_field": f"key {HOSTILE}",
        "message": HOSTILE,
        "severity": "critical",
        "component": "sms-send",
        "node_name": None,
        "environment": "production",
        "provider": "grafana",
        "alert_rule_url": "javascript:alert(1)",
        "row_count": 2,
        "first_seen": END - timedelta(hours=12),
        "last_seen": END,
        "core_rule_ids": (),
        "readiness_rule_ids": ("R9",),
        "evidence": {
            "R9": {
                "rule_id": "R9",
                "matched_rows": 2,
                "sample_evidence": {
                    "severity": "critical",
                    "blocks_completion": True,
                    "reason": "missing",
                },
            }
        },
        "quality_state": "needs_review",
        "llm_principle_id": "P2",
        "llm_confidence": "medium",
        "llm_justification": f"because {HOSTILE}",
        "impact": HOSTILE,
        "runbook_url": 'javascript:alert("runbook")',
        "alert_status": "firing",
        "time_created": None,
        "representative_at": END,
        "attention_rank": 2,
    }
    values.update(overrides)
    return AlertRow(**values)  # type: ignore[arg-type]


def _pages() -> list[str]:
    review = _review()
    alert = _alert()
    decision = Decision("P2", "pending", f"asked {HOSTILE}", END, f"op {HOSTILE}")
    team = team_page(
        [_review(END - timedelta(hours=168), "r2"), review],
        review,
        WorklistPage([alert], 1, 1, 25),
        {(alert.alert_schema, alert.application, alert.key_field): {"P2": decision}},
        show="attention",
        schema="all",
        counts={"attention": 1, "all": 11},
    )
    detail = alert_page(review, AlertDetail(alert, {"P2": [decision]}))
    return [team, detail]


@pytest.mark.parametrize("index", [0, 1], ids=["team", "alert"])
def test_alert_text_is_escaped_and_nothing_executes(index: int) -> None:
    page = _pages()[index]
    assert "<script" not in page
    assert "&lt;script&gt;" in page
    assert " style=" not in page, "inline styles would need unsafe-inline in the CSP"
    assert 'href="javascript:' not in page


def test_an_unusable_runbook_is_shown_as_text_not_a_link() -> None:
    detail = _pages()[1]
    assert "not a usable http(s) link" in detail


def test_the_alert_page_shows_the_decision_to_make_and_marks_the_finding_advisory() -> None:
    detail = _pages()[1]
    assert "Decision needed" in detail
    assert "advisory" in detail
    assert "Readiness gap" in detail and "v2 readiness" in detail
    assert "starts without one" in detail, "a decision never carries to an enriched key"


def test_the_team_page_shows_totals_not_rates_and_no_service_internals() -> None:
    team = _pages()[0]
    assert "distinct alerts this week" in team
    assert "per day" not in team
    for internal in ("run_id", "r3", "registry", "ruleset", "prompt", "model version"):
        assert internal not in team, internal


# ------------------------------------------------------------------ charts


def _point(end: datetime, value: int) -> ChartPoint:
    return ChartPoint(
        label=f"{end:%d %b}",
        value=value,
        window_start=end - timedelta(hours=168),
        window_end=end,
        href=f"/w/{end:%Y-%m-%d}",
        title="week",
    )


def test_consecutive_weeks_are_joined_by_one_line() -> None:
    points = [_point(END - timedelta(hours=168 * i), i) for i in (2, 1, 0)]
    svg = line_chart(points, series="v1", label="x")
    assert svg.count("<polyline") == 1
    assert svg.count('<circle class="dot') == 3


def test_a_gap_breaks_the_line_instead_of_joining_across_it() -> None:
    points = [
        _point(END - timedelta(hours=168 * 3), 5),
        _point(END - timedelta(hours=168 * 2), 6),
        _point(END, 7),
    ]
    svg = line_chart(points, series="v2", label="x")
    assert svg.count("<polyline") == 1, "only the adjacent pair is joined"


def test_a_single_week_has_a_point_and_no_line() -> None:
    svg = line_chart([_point(END, 3)], series="v1", label="x")
    assert "<polyline" not in svg and "dot v1" in svg


def test_chart_labels_are_escaped() -> None:
    point = ChartPoint("<b>", 1, END - timedelta(hours=168), END, "/x?a=1&b=2", "<t>")
    svg = line_chart([point], series="v1", label="<l>")
    assert "<b>" not in svg and "&lt;b&gt;" in svg and "&amp;b=2" in svg


def test_times_are_utc_even_when_the_database_hands_back_naive_values() -> None:
    aware = _review(END.replace(tzinfo=UTC))
    assert aware.week == END.date()
