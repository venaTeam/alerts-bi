"""On-prem OpenAI-compatible adapter (design section 5.1; flow step 6.3).

The regular OpenAI Python SDK against the on-prem base URL, using Chat Completions with
strict JSON-schema output and temperature zero.

SDK automatic retries are disabled (``max_retries=0``). This is not a preference: the
pipeline owns a three-attempt policy and records every attempt, and hidden SDK retries
would silently exceed it and break the audit trail. A timeout or transport error consumes
one recorded attempt like any other failed call.
"""

from __future__ import annotations

from typing import Any

from openai import APITimeoutError, OpenAI, OpenAIError

from alerts_bi.config import LlmConfig
from alerts_bi.llm.client import LlmTransportError
from alerts_bi.llm.response import response_json_schema

__all__ = ["OpenAiLlmClient"]


class OpenAiLlmClient:
    def __init__(self, config: LlmConfig, client: Any | None = None) -> None:
        if not config.base_url:
            raise ValueError("LLM_BASE_URL is required to call the model")
        if not config.model:
            raise ValueError("LLM_MODEL is required to call the model")
        self.config = config
        self.model_version = config.model
        self.client = client or OpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "not-used",
            timeout=config.timeout_ms / 1000,
            max_retries=0,
        )

    def complete(
        self, *, system_prompt: str, request_text: str, batch_id: str, attempt: int
    ) -> str:
        try:
            completion = self.client.chat.completions.create(
                model=self.config.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": request_text},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "alert_batch_verdicts",
                        "strict": True,
                        "schema": response_json_schema(),
                    },
                },
            )
        except APITimeoutError as exc:
            raise LlmTransportError(f"APITimeoutError: {str(exc)[:300]}", "timeout") from exc
        except OpenAIError as exc:
            raise LlmTransportError(f"{type(exc).__name__}: {str(exc)[:300]}", "transport") from exc

        content = completion.choices[0].message.content if completion.choices else None
        if not isinstance(content, str) or content.strip() == "":
            raise LlmTransportError("model returned an empty message", "empty")
        return content
