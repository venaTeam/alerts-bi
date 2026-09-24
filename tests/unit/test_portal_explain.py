"""Plain-language explanations of stored findings (design section 7.10).

Every rule and principle a stored finding can carry must explain itself in words a team can
act on: a work-list reason, why it matched, a next step, and for an uncertain model finding
the decision a person makes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from src.portal.explain import (
    EN_DASH,
    decision_question,
    format_instant,
    format_week,
    principle_next_step,
    principle_title,
    rule_explanation,
)
from src.rules.catalogs import ALL_RULE_IDS, PRINCIPLE_CATALOG, V2_READINESS_RULE_IDS

SAMPLES: dict[str, dict[str, Any]] = {
    "R1": {"field": "message", "normalized": "something went wrong"},
    "R2": {"field": "message", "normalized": "i am alive"},
    "R3": {"violations": [{"field": "application", "reason": "placeholder", "normalized": "test"}]},
    "R4": {"provider": "grafana", "alert_rule_url": None},
    "R5": {"panels": ["team-v1-main"], "reason": "excluded by every supplied panel"},
    "R7": {
        "reason": "older_than_24h",
        "time_created": "2026-08-20T00:00:00Z",
        "timestamp": "2026-08-22T00:00:00.000Z",
    },
    "R8": {"reason": "missing"},
    "R9": {"severity": "critical", "blocks_completion": True, "reason": "missing"},
    "R10": {"field": "impact", "normalized": "high cpu"},
}


@pytest.mark.parametrize("rule_id", ALL_RULE_IDS)
def test_every_rule_explains_itself(rule_id: str) -> None:
    explained = rule_explanation(rule_id, SAMPLES[rule_id])
    for text in (explained.title, explained.reason, explained.why, explained.next_step):
        assert text and text.strip()
    assert explained.readiness == (rule_id in V2_READINESS_RULE_IDS)


def test_the_why_quotes_the_stored_evidence() -> None:
    assert '"something went wrong"' in rule_explanation("R1", SAMPLES["R1"]).why
    assert "team-v1-main" in rule_explanation("R5", SAMPLES["R5"]).why
    assert "2026-08-20T00:00:00Z" in rule_explanation("R7", SAMPLES["R7"]).observed


def test_a_critical_missing_runbook_says_it_blocks_phase_two() -> None:
    assert "blocks phase 2" in rule_explanation("R9", SAMPLES["R9"]).reason
    lenient = rule_explanation(
        "R9", {"severity": "high", "blocks_completion": False, "reason": "missing"}
    )
    assert "blocks" not in lenient.reason
    assert "does not block" in lenient.why


def test_missing_evidence_does_not_break_an_explanation() -> None:
    for rule_id in ALL_RULE_IDS:
        assert rule_explanation(rule_id, None).why


@pytest.mark.parametrize("principle", [p.id for p in PRINCIPLE_CATALOG] + ["OTHER"])
def test_every_principle_has_a_next_step_and_a_decision_question(principle: str) -> None:
    assert principle_next_step(principle)
    question = decision_question(principle)
    assert "onfirm" in question and "ismiss" in question


def test_the_cited_principle_is_shown_in_the_catalogue_wording() -> None:
    for principle in PRINCIPLE_CATALOG:
        assert principle_title(principle.id) == principle.text


def test_a_model_may_cite_a_rule_id_and_still_gets_a_question() -> None:
    assert "Generic message" in decision_question("R1")


def test_dates_are_shown_in_utc_in_one_format() -> None:
    assert format_instant(datetime(2026, 8, 30, 16, 44, 35)) == "30 Aug 2026, 16:44 UTC"
    assert format_week(datetime(2026, 8, 23, 16, 44), datetime(2026, 8, 30, 16, 44)) == (
        f"23 Aug {EN_DASH} 30 Aug 2026"
    )
