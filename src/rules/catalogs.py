"""Versioned phrase catalogues for the deterministic rules (design section 4).

Every entry is already in normalized form: lowercase, single-spaced, no surrounding
punctuation. Adding a phrase changes what a past number meant, so any addition requires a
``RULESET_VERSION`` bump.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final, NamedTuple

__all__ = [
    "ALL_RULE_IDS",
    "CITABLE_IDS",
    "CORE_RULE_IDS",
    "PLACEHOLDER_VALUES",
    "PRINCIPLE_CATALOG",
    "PRINCIPLE_IDS",
    "R1_GENERIC_MESSAGES",
    "R2_HEARTBEAT_MESSAGES",
    "R6_API_MIN_SPAN",
    "R6_API_SPAM_PER_24H",
    "R6_FLAP_CYCLES",
    "R6_FLAP_WINDOW",
    "R6_SPAM_EPISODE_ROWS",
    "R6_STUCK_OPEN",
    "R10_TECHNICAL_CAUSE_IMPACTS",
    "V2_READINESS_RULE_IDS",
    "Principle",
]

#: R1 - generic messages that state nothing about the failure.
R1_GENERIC_MESSAGES: Final = frozenset(
    {
        "error occurred",
        "something went wrong",
        "unable to get data",
        "alert triggered",
        "issue detected",
    }
)

#: R2 - informational / heartbeat messages. "That's a log, not an alert."
R2_HEARTBEAT_MESSAGES: Final = frozenset(
    {
        "i am alive",
        "ok",
        "healthy",
        "started",
        "completed",
        "running",
        "service started",
        "process running",
        "completed successfully",
    }
)

#: R3 / R8 / R9 - placeholder metadata values. Matching is exact whole-value: ``test``
#: matches, ``test-payments-service`` does not.
PLACEHOLDER_VALUES: Final = frozenset({"unknown", "test", "default", "n/a"})

#: R10 - impact values that restate the technical cause instead of the operational
#: symptom. Deliberately narrow: it is the regex proxy for LLM principle P9, so
#: ``high cpu causes checkout latency`` continues to the model rather than matching here.
R10_TECHNICAL_CAUSE_IMPACTS: Final = frozenset(
    {"high cpu", "high cpu usage", "cpu usage is high", "cpu is high"}
)

#: R6 thresholds (design 7.14). Catalogue constants under ``RULESET_VERSION``, never
#: environment settings: changing one changes what a past number meant. R6 judges firing
#: EPISODES, not a repeat interval: Grafana repeat is disabled on both schemas, so a firing
#: alert sends once when it fires and once when it clears.
#: Flapping: fire -> clear cycles inside any rolling window of this length (design 7.14).
R6_FLAP_WINDOW: Final = timedelta(hours=24)
#: Flapping: at least this many cycles inside the window, on any provider.
R6_FLAP_CYCLES: Final = 3
#: Spamming (Grafana): at least this many firing rows in one episode. One notification is
#: expected and one duplicate is tolerated (replicas); three means it is re-sent.
R6_SPAM_EPISODE_ROWS: Final = 3
#: Spamming (non-Grafana): rows per 24 h of span at or above this rate ...
R6_API_SPAM_PER_24H: Final = 24
#: ... and only when the span is at least this long.
R6_API_MIN_SPAN: Final = timedelta(hours=6)
#: Stuck (Grafana): the open episode has lasted at least this long at the window's end.
R6_STUCK_OPEN: Final = timedelta(hours=72)

#: Rule set membership (design section 3.6): core rules read v1 and v2 side by side.
CORE_RULE_IDS: Final = ("R1", "R2", "R3", "R4", "R5", "R6", "R7")

#: V2 readiness gaps. Never enter ``flagged``, never block LLM assessment.
V2_READINESS_RULE_IDS: Final = ("R8", "R9", "R10")

#: R6 (stuck, spamming, flapping) became a core rule in ruleset 1.1.0 (team summary spec
#: section 5). It is evaluated per identity by ``src.rules.firing``, not per row.
ALL_RULE_IDS: Final = CORE_RULE_IDS + V2_READINESS_RULE_IDS


class Principle(NamedTuple):
    id: str
    set: str
    text: str


#: The LLM principle catalogue (design section 4.1). Disjoint from R1-R10, versioned
#: together with it, and both namespaces are legal citations in a verdict.
PRINCIPLE_CATALOG: Final = (
    Principle(
        "P1", "core", "Non-actionable — implies no investigation, fix, escalation or attention"
    ),
    Principle(
        "P2",
        "core",
        'Informational — reports an event or a status rather than a problem ("that\'s a log!")',
    ),
    Principle("P3", "core", "States the outcome, not the failure — something failed, but not what"),
    Principle("P4", "core", "Component or application name does not identify a real thing"),
    Principle("P5", "core", "No environment context — the reader cannot tell where it fired"),
    Principle(
        "P6", "core", "Not grounded in a golden signal (latency / traffic / errors / saturation)"
    ),
    Principle(
        "P7", "v2", "critical that fails the Wake-Up Test (urgent + immediate damage + runbook)"
    ),
    Principle("P8", "v2", "Severity is not derived from impact — the two are incoherent"),
    Principle(
        "P9", "v2", "impact restates the technical cause rather than the operational symptom"
    ),
    Principle(
        "P10",
        "core",
        "The required response is robotic and should have been automated, not alerted",
    ),
    Principle(
        "P11",
        "core",
        "An internal technical cause with no user-visible symptom anywhere in the alert",
    ),
)

#: Every principle id the model may cite.
PRINCIPLE_IDS: Final = tuple(p.id for p in PRINCIPLE_CATALOG)

#: Ids a verdict may cite for ``catalog_violation``: the deterministic rules plus the
#: principles. The model is not confined to R1-R10 - it may cite an R id for something
#: regex missed.
CITABLE_IDS: Final = (
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R6",
    "R7",
    "R8",
    "R9",
    "R10",
    *PRINCIPLE_IDS,
)
