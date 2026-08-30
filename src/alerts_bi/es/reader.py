"""Team-scoped Elasticsearch retrieval (design section 6; flow step 2).

Two separate queries, each restricted to the selected team's configured operators and to
the exact run window. The queries do not restrict ``application``, do not apply the team's
own panel filters, do not read the SQL hot table, and never sweep another team's alerts.

Elasticsearch row ids are read but never persisted as business identity: source documents
expire after three months, so an id is not a durable reference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from alerts_bi.domain.normalize import AlertRecord, normalize_row
from alerts_bi.domain.window import RunWindow
from alerts_bi.es.client import ElasticsearchError, EsClient
from alerts_bi.logging_setup import log
from alerts_bi.registry import TeamEntry
from alerts_bi.timefmt import iso_instant

__all__ = ["V1_INDEX", "V2_INDEX", "ReadResult", "build_query", "read_schema", "read_team_alerts"]

V1_INDEX = "appchi-v1"
V2_INDEX = "appchi-v2"


@dataclass(frozen=True, slots=True)
class ReadResult:
    rows: list[AlertRecord]
    pages: int
    reported_total: int
    """Total hits Elasticsearch reported, used as a paging check."""


def build_query(operators: Sequence[str], window: RunWindow) -> dict[str, Any]:
    """Build the query body for one schema.

    The window is the exact half-open range: ``gte`` window_start, ``lt`` window_end.
    Using ``lte`` would double-count the boundary instant across two adjacent runs.

    ``operator`` is a keyword field, so ``terms`` matches the exact, case-sensitive values
    from the registry - which is what makes ``Checkout-API`` and ``checkout`` two distinct
    entries a team must list separately.
    """
    return {
        "bool": {
            "filter": [
                {"terms": {"operator": list(operators)}},
                {
                    "range": {
                        "@timestamp": {
                            "gte": _iso(window.window_start),
                            "lt": _iso(window.window_end),
                            "format": "strict_date_optional_time",
                        }
                    }
                },
            ]
        }
    }


def _iso(value: datetime) -> str:
    """Render an instant the way Elasticsearch date parsing expects."""
    return iso_instant(value)


def read_schema(
    client: EsClient,
    schema: str,
    operators: Sequence[str],
    window: RunWindow,
    page_size: int | None = None,
) -> ReadResult:
    """Read every matching row for one schema, paging with a PIT and ``search_after``.

    Every row is returned rather than aggregated in the cluster: distinct counts must be
    exact, and ``cardinality`` is HyperLogLog++ and approximate above its threshold.
    Counting identities in Python over the complete paged result is exact by construction.
    """
    index = V1_INDEX if schema == "v1" else V2_INDEX
    size = page_size if page_size is not None else client.config.page_size

    if not operators:
        # Not an error: a migrated team has no v1 operators, and a pre-migration team has
        # no v2 operator. Querying with an empty terms list would match nothing anyway,
        # but skipping makes the intent explicit in the logs.
        log.info("es.skip_schema", schema=schema, reason="no configured operators")
        return ReadResult(rows=[], pages=0, reported_total=0)

    query = build_query(operators, window)
    pit_id = client.open_pit(index)
    rows: list[AlertRecord] = []
    pages = 0
    reported_total = 0
    search_after: list[Any] | None = None

    try:
        while True:
            body: dict[str, Any] = {
                "size": size,
                "track_total_hits": True,
                "query": query,
                # _shard_doc is the PIT tiebreaker: it guarantees a total order, so no
                # document is skipped or repeated across pages even when timestamps
                # collide.
                "sort": [{"@timestamp": "asc"}, {"_shard_doc": "asc"}],
                "pit": {"id": pit_id, "keep_alive": "2m"},
            }
            if search_after is not None:
                body["search_after"] = search_after

            response = client.search(body)
            hits = response.get("hits", {}).get("hits", [])
            if pages == 0:
                reported_total = int(response.get("hits", {}).get("total", {}).get("value", 0))
            pages += 1

            rows.extend(normalize_row(schema, hit["_source"]) for hit in hits)

            if len(hits) < size:
                break
            search_after = hits[-1].get("sort")
            if not search_after:
                raise ElasticsearchError(
                    f"page {pages} of {index} returned no sort values, "
                    "so paging cannot continue safely"
                )
    finally:
        client.close_pit(pit_id)

    # A mismatch means paging lost or duplicated rows, which would silently corrupt every
    # number downstream. Fail rather than publish a partial team scorecard as complete.
    if len(rows) != reported_total:
        raise ElasticsearchError(
            f"{index}: retrieved {len(rows)} rows but the cluster reported "
            f"{reported_total} matching"
        )

    log.info(
        "es.read_schema",
        schema=schema,
        index=index,
        operators=len(operators),
        rows=len(rows),
        pages=pages,
    )
    return ReadResult(rows=rows, pages=pages, reported_total=reported_total)


def read_team_alerts(
    client: EsClient,
    team: TeamEntry,
    window: RunWindow,
    page_size: int | None = None,
) -> dict[str, ReadResult]:
    """Read both schemas for one selected team."""
    v2_operators = [] if team.v2_operator is None else [team.v2_operator]
    return {
        "v1": read_schema(client, "v1", team.v1_operators, window, page_size),
        "v2": read_schema(client, "v2", v2_operators, window, page_size),
    }
