"""HTTP trigger surface (design section 7.8).

A convenience wrapper around exactly what the CLI does, so a run can be started from a
browser or a `curl` line instead of a shell in the repository. It adds no analysis of its
own: every route either loads the registry, calls :func:`~alerts_bi.run.orchestrator.execute_run`
and :func:`~alerts_bi.db.repositories.persist_run` with the same arguments the CLI passes, or
renders a stored run from committed SQL rows. There is no second code path that could drift
from the command line.

Scope discipline is unchanged. A run still names one team and never defaults to all of
them, ``run_at`` is still captured once per run, and the four approved output files are
still written exactly as the CLI writes them.

The package is laid out by responsibility:

* :mod:`~alerts_bi.api.app` - the application factory and the exception handlers
* :mod:`~alerts_bi.api.routers` - the routes, grouped by what they are for
* :mod:`~alerts_bi.api.service` - everything that touches the pipeline, plus the run gate
* :mod:`~alerts_bi.api.schemas` - the wire contract, from which OpenAPI is generated
* :mod:`~alerts_bi.api.dependencies` - typed access to per-application state
* :mod:`~alerts_bi.api.negotiation` - the one rule for HTML versus JSON
* :mod:`~alerts_bi.api.ui` - the two pages the surface renders itself
* :mod:`~alerts_bi.api.server` - running it under uvicorn

Its settings live in :mod:`alerts_bi.config.api` with the rest of the configuration.

SECURITY. There is no authentication. Every request triggers real Elasticsearch queries and
real SQL writes, and a run can call the on-prem model. The listener binds to loopback by
default; widening it exposes an unauthenticated write endpoint to that network.
"""

from __future__ import annotations

from alerts_bi.api.app import build_app
from alerts_bi.api.server import serve
from alerts_bi.api.service import RunGate

__all__ = ["RunGate", "build_app", "serve"]
