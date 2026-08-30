"""LLM assessment orchestration (design section 5.1; flow step 6).

Only identities with no core finding on any row enter this stage; alerts carrying v2
readiness gaps are still eligible. Coverage is exhaustive - there is no classification
budget - so ``unassessed`` is a failure state rather than a design parameter, and any
non-zero value reads as a broken classifier.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from alerts_bi.db.repositories import verdict_key
from alerts_bi.domain.normalize import AlertRecord
from alerts_bi.hashing import compact_json
from alerts_bi.llm.client import LlmClient, LlmTransportError
from alerts_bi.llm.grouping import Batch, build_batches
from alerts_bi.llm.request import assert_lossless, build_request, serialize_request
from alerts_bi.llm.response import LlmResponseError, Verdict, state_for_verdict, validate_response
from alerts_bi.logging_setup import log, redact_error
from alerts_bi.versions import RULESET_VERSION

__all__ = [
    "MAX_ATTEMPTS",
    "AssessmentOutcome",
    "AssessmentResult",
    "assess_alerts",
    "mark_all_unassessed",
]

#: The initial request plus two retries. Not configurable.
MAX_ATTEMPTS: Final = 3


@dataclass(frozen=True, slots=True)
class AssessmentOutcome:
    state: str
    """``llm_flagged`` | ``needs_review`` | ``assessed_good`` | ``unassessed``"""
    principle_id: str | None
    confidence: str | None
    justification: str | None
    unassessed_reason: str | None
    reused: bool
    """True when a durable verdict was reused rather than requested."""
    alert: AlertRecord


@dataclass(slots=True)
class AssessmentResult:
    outcomes: dict[str, AssessmentOutcome] = field(default_factory=dict)
    """Keyed by alert identity."""
    batch_attempts: list[dict[str, Any]] = field(default_factory=list)
    new_verdicts: list[dict[str, Any]] = field(default_factory=list)
    requested_batches: int = 0
    reused_verdicts: int = 0


def _naive(value: datetime) -> datetime:
    """SQL Server DATETIME2 columns take naive UTC values through this driver."""
    return value.replace(tzinfo=None)


def assess_alerts(
    *,
    alerts: Sequence[AlertRecord],
    client: LlmClient,
    system_prompt: str,
    run_id: str,
    prompt_version: str,
    model_version: str,
    now: datetime,
    max_batch_size: int | None = None,
    existing_verdicts: Mapping[str, Mapping[str, Any]] | None = None,
) -> AssessmentResult:
    """Assess every eligible alert."""
    existing = existing_verdicts or {}
    result = AssessmentResult()

    # 6.1 Reuse durable verdicts first. A stored verdict belongs to the prompt and model
    # version that produced it and is never recomputed under the same pair.
    to_request: list[AlertRecord] = []
    for alert in alerts:
        stored = existing.get(verdict_key(alert.application, alert.key_field))
        if stored is None:
            to_request.append(alert)
            continue
        result.outcomes[alert.identity] = AssessmentOutcome(
            state=state_for_verdict(stored),
            principle_id=str(stored["principle_id"]),
            confidence=str(stored["confidence"]),
            justification=str(stored["justification"]),
            unassessed_reason=None,
            reused=True,
            alert=alert,
        )
        result.reused_verdicts += 1

    batches = build_batches(to_request, run_id, prompt_version, model_version, max_batch_size)
    result.requested_batches = len(batches)

    for batch in batches:
        request = build_request(batch, RULESET_VERSION, prompt_version)
        # Losslessness is asserted before the first call, so a factoring bug fails loudly
        # instead of quietly sending the model documents with fields missing.
        assert_lossless(request, batch)

        # Serialized ONCE and reused byte-for-byte on every retry.
        request_text, request_hash = serialize_request(request)

        verdicts, failure_reason = _attempt_batch(
            batch=batch,
            request_text=request_text,
            request_hash=request_hash,
            client=client,
            system_prompt=system_prompt,
            run_id=run_id,
            now=now,
            batch_attempts=result.batch_attempts,
        )

        if verdicts is not None:
            for index, verdict in enumerate(verdicts):
                alert = batch.alerts[index]
                result.outcomes[alert.identity] = AssessmentOutcome(
                    state=state_for_verdict(verdict),
                    principle_id=verdict.principle_id,
                    confidence=verdict.confidence,
                    justification=verdict.justification,
                    unassessed_reason=None,
                    reused=False,
                    alert=alert,
                )
                result.new_verdicts.append(
                    {
                        "application": alert.application,
                        "key_field": alert.key_field,
                        "prompt_version": prompt_version,
                        "model_version": model_version,
                        "alert_schema": alert.schema,
                        "assessment": verdict.assessment,
                        "principle_id": verdict.principle_id,
                        "confidence": verdict.confidence,
                        "justification": verdict.justification,
                        # The source row expires after three months, so the exact document
                        # the model judged is stored here or the verdict is unauditable.
                        "representative_doc": compact_json(alert.source),
                        "doc_hash": alert.doc_hash,
                        "classified_at": _naive(now),
                        "ruleset_version": RULESET_VERSION,
                        "first_run_id": run_id,
                    }
                )
        else:
            # After the third failure, EVERY alert in the batch becomes unassessed with the
            # shared reason. Alerts are never retried individually.
            for alert in batch.alerts:
                result.outcomes[alert.identity] = AssessmentOutcome(
                    state="unassessed",
                    principle_id=None,
                    confidence=None,
                    justification=None,
                    unassessed_reason=failure_reason,
                    reused=False,
                    alert=alert,
                )

    return result


def _attempt_batch(
    *,
    batch: Batch,
    request_text: str,
    request_hash: str,
    client: LlmClient,
    system_prompt: str,
    run_id: str,
    now: datetime,
    batch_attempts: list[dict[str, Any]],
) -> tuple[list[Verdict] | None, str]:
    """Run one batch through at most three attempts.

    Each retry sends the IDENTICAL batch as a unit: same membership, same ordering, same
    bytes. There is no alert-by-alert fallback, because a verdict attached to the wrong
    alert is the worst output this system can produce.
    """
    failure_reason = "unknown failure"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        status = "succeeded"
        reason: str | None = None
        verdicts: list[Verdict] | None = None

        try:
            text = client.complete(
                system_prompt=system_prompt,
                request_text=request_text,
                batch_id=batch.batch_id,
                attempt=attempt,
            )
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LlmResponseError(f"response is not valid JSON: {exc}") from exc

            verdicts = validate_response(parsed, batch.batch_id, batch.alert_ids)
        except LlmTransportError as exc:
            # A timeout or transport error consumes one recorded attempt like any other
            # failed call.
            status = "timeout" if exc.kind == "timeout" else "transport_error"
            reason = redact_error(exc)
            failure_reason = f"{status}: {reason}"
            verdicts = None
        except LlmResponseError as exc:
            status = "invalid_response"
            reason = redact_error(exc)
            failure_reason = f"{status}: {reason}"
            verdicts = None

        batch_attempts.append(
            {
                "run_id": run_id,
                "batch_id": batch.batch_id,
                "attempt_number": attempt,
                "group_type": batch.group_type,
                "group_value": batch.group_value,
                "partition_index": batch.partition_index,
                "partition_count": batch.partition_count,
                "alert_count": len(batch.alerts),
                "alert_ids": compact_json(list(batch.alert_ids)),
                "request_hash": request_hash,
                # The complete payload is auditable data, kept in SQL and never in logs.
                "request_payload": request_text,
                "status": status,
                "failure_reason": None if reason is None else reason[:1000],
                "duration_ms": int((time.monotonic() - started) * 1000),
                "created_at": _naive(now),
            }
        )

        if verdicts is not None:
            log.info(
                "llm.batch_succeeded",
                batch_id=batch.batch_id,
                attempt=attempt,
                alerts=len(batch.alerts),
            )
            return verdicts, ""

        log.warn(
            "llm.batch_attempt_failed", batch_id=batch.batch_id, attempt=attempt, status=status
        )

    log.error(
        "llm.batch_exhausted",
        batch_id=batch.batch_id,
        attempts=MAX_ATTEMPTS,
        alerts=len(batch.alerts),
    )
    return None, f"batch exhausted {MAX_ATTEMPTS} attempts - {failure_reason}"[:500]


def mark_all_unassessed(alerts: Sequence[AlertRecord], reason: str) -> dict[str, AssessmentOutcome]:
    """Mark every eligible identity unassessed without calling a model.

    Used when the run is executed with LLM assessment switched off. The reason is recorded
    per identity so the scorecard never presents "we did not look" as "it is fine".
    """
    return {
        alert.identity: AssessmentOutcome(
            state="unassessed",
            principle_id=None,
            confidence=None,
            justification=None,
            unassessed_reason=reason,
            reused=False,
            alert=alert,
        )
        for alert in alerts
    }
