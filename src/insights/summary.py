"""Assemble a :class:`TeamSummary` from :class:`SummaryInputs`."""

from __future__ import annotations

from src.insights.model import SummaryInputs, TeamSummary


def summarize(inputs: SummaryInputs) -> TeamSummary:
    raise NotImplementedError
