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

Rows sit on 2026-08-20 and 2026-08-21 so both single-date and multi-date allocation are
exercised, well inside the window's partial first and last buckets.
"""

from __future__ import annotations

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


def acceptance_teams() -> list[dict[str, Any]]:
    """Every acceptance team, appended after the realistic ones."""
    return [
        ACCEPTANCE_CORE,
        _batching_team(),
        ACCEPTANCE_SUPPRESSION,
        ACCEPTANCE_BLAST_RADIUS,
    ]
