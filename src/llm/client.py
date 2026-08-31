"""The ``LlmClient`` contract.

One method, deliberately: the pipeline owns the three-attempt policy, the batch identity,
the validation and the persistence, and a client that could retry or reorder on its own
would make those guarantees untrue.

A client returns the raw response TEXT. Parsing and validation happen in the pipeline so
the real adapter and the deterministic fake are held to identical standards.
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["LlmClient", "LlmTransportError"]


class LlmTransportError(Exception):
    """A failed call: transport error, timeout, or an empty message."""

    def __init__(self, message: str, kind: str = "transport") -> None:
        super().__init__(message)
        self.kind = kind


class LlmClient(Protocol):
    """What the pipeline needs from any model transport."""

    model_version: str

    def complete(
        self, *, system_prompt: str, request_text: str, batch_id: str, attempt: int
    ) -> str:
        """Return the raw response text, or raise :class:`LlmTransportError`."""
        ...
