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
from src.portal.pages import safe_link
from src.portal.queries import Review, SchemaTotals

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


def test_the_filter_parameters_are_bounded() -> None:
    """On the Fix list, and on the old addresses whose links still carry filters."""
    paths = ("/teams/x", "/teams/x/weeks/2026-08-30", "/teams/x/weeks/2026-08-30/fix")
    with TestClient(build_portal(SETTINGS), client=("10.1.2.3", 50000)) as client:
        for path in paths:
            for params in ({"state": "evil"}, {"rule": "R11"}, {"rule": "R1' OR 1=1"}):
                assert client.get(path, params=params).status_code == 422, (path, params)
        picked = client.get("/teams/x/weeks", params={"week": "2026-08-30", "tab": "evil"})
        assert picked.status_code == 422


def test_every_tab_is_its_own_read_only_address() -> None:
    app = build_portal(SETTINGS)
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    for tab in ("fix", "volume", "dashboards", "migration", "history", "slides", "alert"):
        assert f"/teams/{{team_id}}/weeks/{{week}}/{tab}" in paths, tab


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
