"""Time to retire v1 (spec section 7): a measured-pace date and a configured-effort figure."""

from __future__ import annotations

from src.insights.model import AlertRow, Estimate, SummaryInputs


def v1_rule_key(alert: AlertRow) -> str:
    """The unit of migration work: ``url:<alert_rule_url>`` or ``app:<application>``."""
    raise NotImplementedError


def estimate(inputs: SummaryInputs) -> Estimate:
    """Never stored, never in an output file. See spec 7.1 and 7.2."""
    raise NotImplementedError
