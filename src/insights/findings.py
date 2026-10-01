"""Key-findings sentences (spec section 4): templated from stored counts, never model text."""

from __future__ import annotations

from src.insights.model import KeyFinding, SummaryInputs


def key_findings(inputs: SummaryInputs) -> tuple[KeyFinding, ...]:
    raise NotImplementedError
