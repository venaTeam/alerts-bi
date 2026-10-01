"""Assemble a :class:`TeamSummary` from :class:`SummaryInputs`."""

from __future__ import annotations

from src.insights.aggregate import biggest, by_application, fire_rows
from src.insights.estimate import estimate
from src.insights.findings import key_findings
from src.insights.model import SummaryInputs, TeamSummary


def summarize(inputs: SummaryInputs) -> TeamSummary:
    return TeamSummary(
        inputs=inputs,
        key_findings=key_findings(inputs),
        by_application=by_application(inputs.alerts),
        fire=fire_rows(inputs.alerts),
        biggest=biggest(inputs.alerts),
        estimate=estimate(inputs),
    )
