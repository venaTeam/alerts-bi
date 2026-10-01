"""Acceptance fixture teams for the alerts BI MVP.

These are NOT a second fixture system: they are team definitions consumed by
``generate_mock_alerts.py`` alongside the seven realistic teams, and they load into the
same ``appchi-v1`` / ``appchi-v2`` indices. They are separated into this file only because
they are authored to a different standard - every row is pinned to an exact timestamp and
an exact expected outcome, so ``test/fixtures/expected-results.json`` can be computed by
hand from these definitions.

The acceptance window is the exact 168 hours ending at the generator's fixed clock:
  run_at       2026-08-25T18:00:00Z
  window_start 2026-08-18T18:00:00Z (inclusive)
  window_end   2026-08-25T18:00:00Z (exclusive)

The first four teams' rows sit on 2026-08-20 and 2026-08-21 so both single-date and
multi-date allocation are exercised, well inside the window's partial first and last
buckets. ``acceptance-fire-patterns`` needs days of continuous firing for R6, so its rows
run from 2026-08-19 to 2026-08-24 - still entirely inside the window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

__all__ = ["T20", "T21", "acceptance_teams"]

#: Every acceptance row lands on one of these two instants.
T20 = "2026-08-20T12:00:00.000Z"
T21 = "2026-08-21T12:00:00.000Z"

RULE_URL = "https://grafana.internal/d/acc-core-1"
GOOD_V1_MESSAGE = "Checkout error rate above 2% of requests over 5m"
GOOD_V2_MESSAGE = "p99 checkout latency above 900ms over 10m"
GOOD_IMPACT = "Customers see slow or failing checkouts"
GOOD_RUNBOOK = "https://runbooks.internal/acceptance/checkout"


def _v1(obj: str, **overrides: Any) -> dict[str, Any]:
    return {
        "application": "acc-app",
        "obj": obj,
        "node_name": "node-a",
        "message": GOOD_V1_MESSAGE,
        "operatorPick": "acc-core",
        "alert_rule_url": RULE_URL,
        "rowsAt": [T20],
        **overrides,
    }


def _v2(obj: str, **overrides: Any) -> dict[str, Any]:
    return {
        "application": "acc-app-v2",
        "obj": obj,
        "message": GOOD_V2_MESSAGE,
        "severity": "high",
        "impact": GOOD_IMPACT,
        "runbook_url": GOOD_RUNBOOK,
        "alert_rule_url": RULE_URL,
        "rowsAt": [T20],
        **overrides,
    }


#: acceptance-core - one row per case, so every deterministic rule boundary is countable
#: by eye. Each v1 alert uses a distinct ``obj``, which makes each one a distinct identity
#: under the v1 key (application + object + node_name).
ACCEPTANCE_CORE: dict[str, Any] = {
    "name": "acceptance-core",
    "phase": "acceptance",
    "quality": "mixed",
    "schemas": ["v1", "v2"],
    "v1Operators": ["acc-core"],
    "v2Operator": "acc-core-v2",
    "v1PanelQuery": None,
    "v2PanelQuery": None,
    "v1Defs": [
        # --- clean: no core finding, so these go to the model ---
        _v1("c01-clean", provider="grafana"),
        # R4 does NOT apply to API alerts: no rule URL is not evidence against them.
        _v1("c02-api-no-url", alert_rule_url=None, provider="api"),
        # R7 inclusive boundaries: both valid, neither flagged.
        _v1("c03-r7-equal", timeCreated="equal"),
        _v1("c04-r7-oldest", timeCreated="oldest"),
        # --- one core finding each ---
        _v1("c05-r1", message="Error Occurred"),
        _v1("c06-r2", message="i am alive"),
        _v1("Unknown"),
        _v1("c08-r4", alert_rule_url=None, provider="grafana"),
        _v1("c09-r7-future", timeCreated="future"),
        _v1("c10-r7-stale", timeCreated="stale"),
        # --- multi-row identity spanning two dates: three rows, one identity ---
        # The R1 match is on the 08-21 row only, which proves findings are not projected
        # onto the 08-20 rows that did not match, and that one core finding anywhere in the
        # window still withholds the whole identity from the model.
        _v1("c11-multiday", message="Alert triggered", rowsAt=[T21]),
        _v1("c11-multiday", rowsAt=[T20, T20]),
    ],
    "v2Defs": [
        # completion-ready
        _v2("v01-ready"),
        # R8: missing impact
        _v2("v02-r8", severity="warning", impact=None),
        # R9 on critical: blocks phase-2 completion
        _v2("v03-r9-critical", severity="critical", runbook_url=None),
        # R9 on high: visible, but does NOT reduce readiness
        _v2("v04-r9-high", runbook_url=None),
        # R10: impact restates the technical cause
        _v2("v05-r10", severity="warning", impact="high cpu"),
        # R2 core finding on v2: core rules apply to both schemas
        _v2("v06-r2", severity="warning", message="completed successfully"),
    ],
}


def _batching_team() -> dict[str, Any]:
    """acceptance-batching - the grouping and partition paths.

    401 v2 identities share ONE alert_rule_url, so the group must split into balanced
    partitions of 134/134/133. A separate set of API alerts carries no rule URL and must
    fall back to application grouping without ever merging with the rule-URL group.
    """
    big_rule_url = "https://grafana.internal/d/acc-batch-big"
    v2_defs: list[dict[str, Any]] = []

    for index in range(401):
        suffix = f"{index:04d}"
        v2_defs.append(
            {
                "application": "acc-batch-app",
                "obj": f"big-{suffix}",
                "message": f"Queue depth above threshold on shard {suffix}",
                "severity": "warning",
                "impact": "Processing for this shard falls behind",
                "runbook_url": "https://runbooks.internal/acceptance/batch",
                "alert_rule_url": big_rule_url,
                "key_field": f"acc-batch-big-{suffix}",
                "rowsAt": [T20],
            }
        )

    for index in range(3):
        v2_defs.append(
            {
                "application": "acc-batch-api-app",
                "obj": f"api-{index}",
                "message": f"API-sent alert {index}: ingest lag above 5m",
                "severity": "warning",
                "impact": "Ingest results arrive late for this stream",
                "runbook_url": "https://runbooks.internal/acceptance/ingest",
                "alert_rule_url": None,
                "provider": "api",
                "key_field": f"acc-batch-api-{index}",
                "rowsAt": [T20],
            }
        )

    return {
        "name": "acceptance-batching",
        "phase": "acceptance",
        "quality": "good",
        "schemas": ["v2"],
        "v2Operator": "acc-batching",
        "v1PanelQuery": None,
        "v2PanelQuery": None,
        "v2Defs": v2_defs,
    }


#: acceptance-suppression - every suppression safety path in one team.
#:
#: The registry gives this team two v1 panels so multi-panel unanimity is exercised: a row
#: hidden by both is suppressed, a row hidden by only one is not.
ACCEPTANCE_SUPPRESSION: dict[str, Any] = {
    "name": "acceptance-suppression",
    "phase": "acceptance",
    "quality": "mixed",
    "schemas": ["v1"],
    "v1Operators": ["acc-suppression"],
    "v1PanelQuery": None,
    "v2PanelQuery": None,
    "v1Defs": [
        # Hidden by BOTH panels -> suppressed (R5).
        {
            "application": "acc-sup-app",
            "obj": "s01",
            "node_name": "junk-node",
            "message": GOOD_V1_MESSAGE,
            "operatorPick": "acc-suppression",
            "alert_rule_url": RULE_URL,
            "rowsAt": [T20],
        },
        # Hidden by panel A only (its message matches A's NOT LIKE) -> NOT suppressed.
        {
            "application": "acc-sup-app",
            "obj": "s02",
            "node_name": "real-node-1",
            "message": "canary probe reported a fault",
            "operatorPick": "acc-suppression",
            "alert_rule_url": RULE_URL,
            "rowsAt": [T20],
        },
        # Named only inside panel B's OR-nested leaf -> unmeasured, never suppressed.
        {
            "application": "acc-sup-app",
            "obj": "s03",
            "node_name": "or-nested-node",
            "message": GOOD_V1_MESSAGE,
            "operatorPick": "acc-suppression",
            "alert_rule_url": RULE_URL,
            "rowsAt": [T20],
        },
        # Named only by an unresolved query variable -> unmeasured, never suppressed.
        {
            "application": "acc-sup-app",
            "obj": "s04",
            "node_name": "query-var-node",
            "message": GOOD_V1_MESSAGE,
            "operatorPick": "acc-suppression",
            "alert_rule_url": RULE_URL,
            "rowsAt": [T20],
        },
        # Visible in both panels.
        {
            "application": "acc-sup-app",
            "obj": "s05",
            "node_name": "real-node-2",
            "message": GOOD_V1_MESSAGE,
            "operatorPick": "acc-suppression",
            "alert_rule_url": RULE_URL,
            "rowsAt": [T20],
        },
        {
            "application": "acc-sup-app",
            "obj": "s06",
            "node_name": "real-node-3",
            "message": GOOD_V1_MESSAGE,
            "operatorPick": "acc-suppression",
            "alert_rule_url": RULE_URL,
            "rowsAt": [T20],
        },
    ],
}


#: acceptance-blast-radius - a single panel whose exclusion list reaches more than half the
#: team's owned rows, which the guard must refuse to apply.
#:
#: 3 of 5 rows (60%) are named by the exclusion, so the leaf becomes unmeasured and NO row
#: is suppressed.
ACCEPTANCE_BLAST_RADIUS: dict[str, Any] = {
    "name": "acceptance-blast-radius",
    "phase": "acceptance",
    "quality": "mixed",
    "schemas": ["v1"],
    "v1Operators": ["acc-blast"],
    "v1PanelQuery": None,
    "v2PanelQuery": None,
    "v1Defs": [
        {
            "application": "acc-blast-app",
            "obj": obj,
            "node_name": f"blast-node-{index + 1}",
            "message": GOOD_V1_MESSAGE,
            "operatorPick": "acc-blast",
            "alert_rule_url": RULE_URL,
            "rowsAt": [T20],
        }
        for index, obj in enumerate(["b01", "b02", "b03", "b04", "b05"])
    ],
}


FIVE_MINUTES = timedelta(minutes=5)
FIRE_URL = "https://grafana.internal/d/acc-fire-1"
FIRE_V2_URL = "https://grafana.internal/d/acc-fire-v2"


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _every(start: str, step: timedelta, count: int) -> list[str]:
    """``count`` instants from ``start`` (inclusive), ``step`` apart."""
    first = _at(start)
    return [_iso(first + index * step) for index in range(count)]


def _fire(obj: str, rows_at: list[str], **overrides: Any) -> dict[str, Any]:
    return {
        "application": "acc-fire-app",
        "obj": obj,
        "node_name": "node-f",
        "message": GOOD_V1_MESSAGE,
        "operatorPick": "acc-fire",
        "alert_rule_url": FIRE_URL,
        "provider": "grafana",
        "rowsAt": rows_at,
        **overrides,
    }


def _fire_api(obj: str, rows_at: list[str]) -> dict[str, Any]:
    # API alerts carry no rule URL (R4 is Grafana-only) and have no repeat interval.
    return _fire(obj, rows_at, application="acc-fire-api", alert_rule_url=None, provider="api")


def _fire_v2_episode(fired: str, resolved: str) -> dict[str, Any]:
    # One fire -> resolve cycle. `resolves` marks the def's LAST row resolved, and the v2 key
    # excludes status, so the three episode defs below are one identity; the explicit
    # key_field just makes that identity readable in the oracle.
    return {
        "application": "acc-fire-app-v2",
        "obj": "f-h-flap",
        "message": GOOD_V2_MESSAGE,
        "severity": "high",
        "impact": GOOD_IMPACT,
        "runbook_url": GOOD_RUNBOOK,
        "alert_rule_url": FIRE_V2_URL,
        "provider": "grafana",
        "key_field": "acc-fire-h-flap-v2",
        "rowsAt": [fired, resolved],
        "resolves": True,
    }


#: acceptance-fire-patterns - R6 boundaries (team summary spec section 5).
#:
#: One identity per case; every v1 case is a distinct ``obj``. A v1 clear is a row with
#: severity ``clear`` (code 1) under the SAME application/object/node_name - the v1 key does
#: not include severity - so a fire -> clear sequence is two defs sharing one identity.
#: Span = last - first + repeat interval (v1 5 min, v2 12 h).
ACCEPTANCE_FIRE_PATTERNS: dict[str, Any] = {
    "name": "acceptance-fire-patterns",
    "phase": "acceptance",
    "quality": "mixed",
    "schemas": ["v1", "v2"],
    "v1Operators": ["acc-fire"],
    "v2Operator": "acc-fire-v2",
    "v1PanelQuery": None,
    "v2PanelQuery": None,
    "v1Defs": [
        # (a) stuck: every 5 min from 08-21 00:00 through 08-24 00:00 = 865 rows, span
        #     exactly 72h05m, ratio 1.0, no clear.
        _fire("f-a-stuck", _every("2026-08-21T00:00:00.000Z", FIVE_MINUTES, 865)),
        # (b) none: every 5 min from 08-21 00:00 through 08-23 23:00 = 853 rows, span
        #     71h05m < 72h.
        _fire("f-b-short", _every("2026-08-21T00:00:00.000Z", FIVE_MINUTES, 853)),
        # (c) spamming: two definitions with one key firing on the SAME 5-minute ticks for
        #     24h. 576 rows over a span of exactly 24h (288 intervals): ratio exactly 2.0,
        #     the inclusive boundary.
        _fire("f-c-dup", _every("2026-08-20T00:00:00.000Z", FIVE_MINUTES, 288)),
        _fire("f-c-dup", _every("2026-08-20T00:00:00.000Z", FIVE_MINUTES, 288)),
        # (c2) none: the same two definitions interleaved at a 2.5-minute offset instead.
        #     576 rows over a span of 24h02m30s (288.5 intervals): ratio 2n/(n+1) =
        #     576/288.5 = 1.9965 < 2.0. Interleaving can never reach 2.0; pinned here so
        #     that property is visible rather than surprising.
        _fire("f-c2-offset", _every("2026-08-19T00:00:00.000Z", FIVE_MINUTES, 288)),
        _fire("f-c2-offset", _every("2026-08-19T00:02:30.000Z", FIVE_MINUTES, 288)),
        # (d) flapping: three fire -> clear cycles on 08-24, clears at 00:05, 10:05 and 20:05
        #     (20h apart, inside one rolling 24h).
        _fire(
            "f-d-flap",
            ["2026-08-24T00:00:00.000Z", "2026-08-24T10:00:00.000Z", "2026-08-24T20:00:00.000Z"],
        ),
        _fire(
            "f-d-flap",
            ["2026-08-24T00:05:00.000Z", "2026-08-24T10:05:00.000Z", "2026-08-24T20:05:00.000Z"],
            severity="clear",
        ),
        # (e) none: three cycles with clears at 08-22 18:05, 08-23 07:05 and 08-23 20:05 -
        #     26h from first to last clear, so no rolling 24h holds more than two.
        _fire(
            "f-e-spread",
            ["2026-08-22T18:00:00.000Z", "2026-08-23T07:00:00.000Z", "2026-08-23T20:00:00.000Z"],
        ),
        _fire(
            "f-e-spread",
            ["2026-08-22T18:05:00.000Z", "2026-08-23T07:05:00.000Z", "2026-08-23T20:05:00.000Z"],
            severity="clear",
        ),
        # (f) spamming: v1 API, 24 rows - hourly 00:00..22:00 plus 23:55 on 08-22. Span
        #     23h55m + 5m = exactly 24h, so exactly 24 events per 24h, the inclusive
        #     boundary, with span >= 6h.
        _fire_api(
            "f-f-api24",
            [
                *_every("2026-08-22T00:00:00.000Z", timedelta(hours=1), 23),
                "2026-08-22T23:55:00.000Z",
            ],
        ),
        # (g) none: v1 API, 23 rows over the same 24h span - hourly 00:00..21:00 plus 23:55.
        _fire_api(
            "f-g-api23",
            [
                *_every("2026-08-22T00:00:00.000Z", timedelta(hours=1), 22),
                "2026-08-22T23:55:00.000Z",
            ],
        ),
    ],
    "v2Defs": [
        # (h) flapping: v2 Grafana with per-row `resolved`. One long firing row on 08-21,
        #     then resolved at 08-23 02:00, 08:00 and 14:00 - three cycles inside 12h.
        #     Span 62h (50h + 12h) gives ratio 6/(62/12) = 1.16, deliberately below 2.0, so
        #     flapping is the ONLY pattern that can match: if per-row resolved were not
        #     read, this identity would be R6-free and acceptance would fail.
        _fire_v2_episode("2026-08-21T12:00:00.000Z", "2026-08-23T02:00:00.000Z"),
        _fire_v2_episode("2026-08-23T06:00:00.000Z", "2026-08-23T08:00:00.000Z"),
        _fire_v2_episode("2026-08-23T12:00:00.000Z", "2026-08-23T14:00:00.000Z"),
    ],
}


UNSEEN_URL = "https://grafana.internal/d/acc-unseen-1"
UNSEEN_V2_URL = "https://grafana.internal/d/acc-unseen-v2"


def _unseen_v1(obj: str, application: str, node_name: str, rows_at: list[str]) -> dict[str, Any]:
    return {
        "application": application,
        "obj": obj,
        "node_name": node_name,
        "message": GOOD_V1_MESSAGE,
        "operatorPick": "acc-unseen",
        "alert_rule_url": UNSEEN_URL,
        "provider": "grafana",
        "rowsAt": rows_at,
    }


def _unseen_v2(obj: str) -> dict[str, Any]:
    return {
        **_v2(obj),
        "application": "acc-unseen-app-v2",
        "alert_rule_url": UNSEEN_V2_URL,
        "key_field": f"acc-unseen-{obj}",
    }


#: acceptance-unseen - the ``unseen`` visibility measure (team summary spec section 6).
#:
#: The registry gives this team ONE v1 panel and no v2 panel:
#:   WHERE operator = 'acc-unseen' AND application = 'shown-app'
#:     AND (node_name = 'n1' OR severity = 'critical') AND node_name != 'junk'
#: ``application = 'shown-app'`` is the identity leaf that hides rows; the OR-nested
#: ``node_name = 'n1'`` is unmeasured and never hides; ``node_name != 'junk'`` suppresses.
ACCEPTANCE_UNSEEN: dict[str, Any] = {
    "name": "acceptance-unseen",
    "phase": "acceptance",
    "quality": "mixed",
    "schemas": ["v1", "v2"],
    "v1Operators": ["acc-unseen"],
    "v2Operator": "acc-unseen-v2",
    "v1PanelQuery": None,
    "v2PanelQuery": None,
    "v1Defs": [
        # Outside the panel's application narrowing -> unseen, on both dates.
        _unseen_v1("u01-other", "other-app", "n1", [T20, T21]),
        # Inside the narrowing -> seen.
        _unseen_v1("u02-shown", "shown-app", "n1", [T20, T21]),
        # Excluded by node_name != 'junk' -> suppressed (R5), not unseen.
        _unseen_v1("u03-junk", "shown-app", "junk", [T20]),
        # Fails only the OR-nested node_name = 'n1' leaf, which is unmeasured -> seen.
        _unseen_v1("u04-or", "shown-app", "n2", [T20]),
        # Outside the narrowing AND suppressed -> counted only as suppressed.
        _unseen_v1("u05-other-junk", "other-app", "junk", [T20]),
    ],
    # No v2 panel, so v2 `unseen` is NULL rather than 0.
    "v2Defs": [_unseen_v2("uv01"), _unseen_v2("uv02")],
}


def acceptance_teams() -> list[dict[str, Any]]:
    """Every acceptance team, appended after the realistic ones.

    New teams go at the END: nothing here draws from the seeded RNG (every def pins its
    operator and its timestamps), but appending keeps that guarantee independent of it.
    """
    return [
        ACCEPTANCE_CORE,
        _batching_team(),
        ACCEPTANCE_SUPPRESSION,
        ACCEPTANCE_BLAST_RADIUS,
        ACCEPTANCE_FIRE_PATTERNS,
        ACCEPTANCE_UNSEEN,
    ]
