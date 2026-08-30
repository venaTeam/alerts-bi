"""HTTP routes, grouped by what they are for."""

from __future__ import annotations

from alerts_bi.api.routers import meta, runs

__all__ = ["meta", "runs"]
