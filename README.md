# Alerts BI

Measures one team's alerting for one week and hands that team a concrete list of what to
fix.

A run selects **one** team, reads its last 168 hours from Elasticsearch, applies the
deterministic rule set, measures how much of its own inventory the team hides from its
dashboards, asks an on-prem model about everything the rules could not decide, persists the
result to SQL Server, and renders a scorecard from the stored rows.

**The tool reports numbers; people draw conclusions.** Every figure is a statement about a
single week. There is no comparison against a previous run, no trend, no baseline and no
cross-team leaderboard — by design.

[`docs/alerts_bi_design.md`](docs/alerts_bi_design.md) is the canonical specification.
[`docs/alerts_bi_flow.md`](docs/alerts_bi_flow.md) describes one run end to end, and
[`docs/alerts_bi_implementation_plan.md`](docs/alerts_bi_implementation_plan.md) describes
what to build. Where this README and the design differ, the design wins.

The implementation language is **Python** (design section 7.7). The superseded JavaScript
implementation was the behavioural reference for the port and is preserved at the
`javascript-mvp` tag. Both were run over the same fixture and compared row for row before
it was removed; design section 7.7 records the result.

---

## Prerequisites

- **Python 3.12 or later**
- [`uv`](https://docs.astral.sh/uv/) for dependency management and the committed lockfile
- Docker, for the Elasticsearch, Kibana and SQL Server stack

Node.js is **not** required for anything: build, tests, mock seeding and runtime are all
Python.

## Quick start

```bash
uv sync
```

```bash
cp .env.example .env
```

Set `MSSQL_SA_PASSWORD` and `SQL_PASSWORD` to the same value in `.env` — SQL Server
requires at least 8 characters with upper, lower, digit and symbol. The Compose file
refuses to start without it rather than falling back to a default password.

```bash
docker compose up -d
```

That starts Elasticsearch 8.15 (`localhost:9200`), Kibana (`localhost:5601`) and SQL
Server 2022 (`localhost:1433`). Wait for all three to report healthy:

```bash
docker compose ps
```

Load the mock dataset and apply the migrations:

```bash
RESET=1 uv run python scripts/generate_mock_alerts.py
```

```bash
uv run alerts-bi db migrate
```

Run a team:

```bash
uv run alerts-bi run --team checkout-api --run-at 2026-08-25T18:00:00Z --fake-llm
```

The scorecard and the three CSV exports are written under `out/<run id prefix>/`.

`--run-at` is needed against the mock because its dataset is generated on a fixed clock
(2026-08-25T18:00:00Z). A production run omits it and uses the current time.

---

## Commands

| Command | What it does |
|---|---|
| `alerts-bi run --team <id>` | Analyse one team, persist the run, render the report |
| `alerts-bi report --run-id <id>` | Re-render a stored run without recomputing anything |
| `alerts-bi report --team <id>` | Re-render that team's most recent completed run |
| `alerts-bi db migrate` | Create the database if absent and apply pending migrations |
| `alerts-bi db status` | Show which migrations are applied |
| `alerts-bi db reset-test` | Drop and recreate **only** the configured disposable test database |
| `alerts-bi verify-acceptance` | Compare persisted rows and CSVs against the hand-reviewed manifest |
| `alerts-bi serve` | Serve the HTTP trigger surface (see below) |

### `run` options

| Flag | Meaning |
|---|---|
| `--team <id>` | Required. A run never defaults to all teams. |
| `--run-at <iso>` | Freeze `run_at`. Defaults to now. |
| `--out <dir>` | Output directory. Default `out/<run id prefix>`. |
| `--fake-llm` | Use the deterministic fake client instead of the on-prem model. |
| `--no-llm` | Skip assessment entirely; eligible identities become `unassessed`. |
| `--registry <path>` | Registry file. Default `config/teams.json`. |
| `--database <name>` | Target database. Default `SQL_DATABASE`. |

`--fake-llm` stamps its own `model_version` onto the run record, so a mock run can never be
mistaken for a live one.

---

## The HTTP trigger surface

A convenience wrapper around the same pipeline the CLI drives, so a run can be started from
a browser instead of a shell in the repository.

```bash
uv run alerts-bi serve
```

Then open <http://127.0.0.1:8000>, pick a team and press Run. The response **is** that run's
scorecard.

| Route | What it does |
|---|---|
| `GET /` | Team list and a run form |
| `GET /healthz` | Liveness, with Elasticsearch and SQL Server reported separately |
| `GET /teams` | The registry's teams as JSON |
| `POST /runs` | Run one team; returns the scorecard HTML |
| `GET /runs/<run_id>` | Re-render that run's scorecard from SQL |
| `GET /runs/latest?team=<id>` | The team's most recent completed run |
| `GET /runs/<run_id>/<file>.csv` | One of the three CSV exports |
| `GET /docs`, `/redoc`, `/openapi.json` | Generated API documentation and schema |

`POST /runs` takes a JSON body: `team` (required), `run_at` (optional ISO 8601) and `llm`
(`live`, `fake` or `off`, mirroring the CLI's default, `--fake-llm` and `--no-llm`). Send
`Accept: application/json` to get a summary with links instead of the scorecard HTML.

```bash
curl -X POST -H "Content-Type: application/json" -H "Accept: application/json" -d '{"team":"checkout-api","run_at":"2026-08-25T18:00:00Z","llm":"fake"}' http://127.0.0.1:8000/runs
```

```bash
curl -o scorecard.html -X POST -H "Content-Type: application/json" -d '{"team":"checkout-api","llm":"fake"}' http://127.0.0.1:8000/runs
```

The request and response shapes are declared as Pydantic models, so the OpenAPI document is
generated from the code rather than maintained beside it: interactive documentation at
`/docs` and `/redoc`, the schema at `/openapi.json`.

The surface adds no analysis. It loads the registry, calls the same `execute_run` and
`persist_run` the CLI calls, writes the same four files under `out/`, and renders reports
from committed SQL rows. A run still names one team and never defaults to all of them.

Built with **FastAPI** on **uvicorn**. The run endpoint is a plain `def`, so FastAPI
dispatches its minutes of blocking Elasticsearch and SQL work to the thread pool instead of
stalling the event loop.

Runs are **serialized**: a second request while one is running gets `409`, because two runs
of the same team and clock derive one deterministic `run_id` and would race to replace each
other's rows.

**There is no authentication.** Every request triggers real Elasticsearch reads and real SQL
writes, and a `live` run can call the on-prem model. The listener binds to `127.0.0.1` by
default; `--host` widens it, and on a shared machine that exposes an unauthenticated write
endpoint to the network.

---

## What a run produces

Exactly four files, and no others:

- `scorecard.html` — self-contained, no scripts and no external resources
- `daily_metrics.csv`
- `rule_counts.csv`
- `alert_worklist.csv`

All four are rendered **only from committed SQL rows**. Nothing is recomputed from
Elasticsearch, and nothing is rendered from in-memory pipeline results. Rendering is a
separate, retryable step, so a display failure after a successful run loses nothing:

```bash
uv run alerts-bi report --run-id <run id>
```

### Reading the numbers

Two counts always travel together. `alerts` is the raw row count — pipeline and dashboard
load. `distinct_alerts` is the count of distinct `application + key_field` identities — how
many things actually fired. One stuck v1 alert is roughly 288 rows a day and one distinct
alert; a team genuinely flooding the pipeline looks completely different. A team needs the
first number to care and the second to act.

**Every distinct figure is published as a per-day rate**, never as a window total, because
a 7-day total is 7× a 1-day total for arithmetic reasons alone.

**v1 and v2 row counts are never added together.** v1 re-fires a still-active alert every
5 minutes and v2 every 12 hours, so moving one alert between schemas divides its row count
by 144 without anyone improving anything.

`good` is `assessed_good`. It is never `alerts - flagged`, because that would count
everything nobody examined as fine. `unassessed` is reported next to it and should be zero.

---

## Configuration

All configuration is environment-based; see `.env.example` for the full list. `.env` is
never committed.

| Variable | Purpose |
|---|---|
| `ES_URL`, `ES_USERNAME`, `ES_PASSWORD` | Elasticsearch endpoint and basic auth |
| `ES_PAGE_SIZE` | Page size for point-in-time pagination |
| `SQL_HOST`, `SQL_PORT`, `SQL_USER`, `SQL_PASSWORD` | SQL Server connection, via SQLAlchemy over `mssql+pymssql` |
| `SQL_DATABASE` | Persistent store, default `alerts_bi_dev` |
| `SQL_TEST_DATABASE` | Disposable test database, default `alerts_bi_test` |
| `LLM_ENABLED` | Must be true for a run to call the on-prem model |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | On-prem OpenAI-compatible endpoint |
| `LLM_TIMEOUT_MS` | Per-attempt timeout; a timeout consumes one of the three attempts |
| `LLM_MAX_BATCH_SIZE` | May lower the 200-alert ceiling, never raise it |
| `API_HOST`, `API_PORT` | Where `alerts-bi serve` listens; `--host` / `--port` override |
| `API_REGISTRY_PATH`, `API_DATABASE`, `API_OUT_DIR` | Surface overrides for the registry, target database and report directory |

For an on-prem cluster with a private CA, set `ES_CA_CERT` to the bundle path; the
Elasticsearch Python client takes it directly.

Alert documents, credentials and complete LLM payloads never appear in logs. Logs carry
identifiers, hashes, counts, timings and redacted errors. Auditable payloads are stored in
SQL only.

---

## The team registry

Ownership is **supplied, never inferred**. `config/teams.json` maps each team to its exact
v1 `operator` values and its v2 `operator`, and is validated in full against
`config/teams.schema.json` before any Elasticsearch query runs.

```json
{
  "team_id": "checkout-api",
  "display_name": "Checkout API",
  "v1_operators": ["checkout", "Checkout-API"],
  "v2_operator": "checkout-api",
  "panels": [{ "panel_id": "checkout-api-v1-main", "schema": "v1", "sql": "SELECT ..." }]
}
```

Operator matching is exact and **case-sensitive**, which is why a team using both
`checkout` and `Checkout-API` must list both. No operator may belong to two teams, though
one team may carry the same string as both its v1 and v2 operator. Every run records the
registry version, the SHA-256 of the complete registry file, and an immutable snapshot of
the selected entry, so the ownership used is reproducible even after the registry is
edited.

Panels are optional and are used for exactly one thing: finding the predicates by which a
team filters its own alerts out of its own dashboards. **A panel never establishes
ownership** and never narrows the alerts a run counts.

---

## Testing

```bash
uv run pytest tests/unit
```

```bash
uv run pytest tests/integration
```

```bash
uv run pytest tests/acceptance
```

Unit tests need nothing running. Integration and acceptance tests need Docker Compose up
and the mock dataset loaded; they skip with an explanatory message otherwise, rather than
failing.

Integration and acceptance tests use the **disposable** `alerts_bi_test` database, which
they recreate. `alerts-bi db reset-test` refuses any target that is not the configured test database
and additionally requires `test` in the name, so a mistyped environment variable cannot
take out the development store.

Tests never call the network for LLM assessment. They use a deterministic fake client
scripted by `(batch_id, attempt)`, which is what makes "the second attempt succeeds" and
"all three attempts fail" expressible without timing or randomness. Live endpoint
validation is separate and opt-in.

### Acceptance verification

```bash
uv run alerts-bi verify-acceptance
```

Runs the four acceptance teams and compares the persisted SQL rows and the rendered CSV
exports against `test/fixtures/expected-results.json`.

That manifest is **hand-authored** from the fixture definitions in
`scripts/acceptance_teams.py`, with the derivation of every number recorded alongside it.
The pipeline does not generate its own oracle: an oracle produced by the code under test
would agree with any bug that happened to be self-consistent.

The full checks (`uv run ruff format --check .`, `uv run ruff check .`,
`uv run mypy src`, all three test suites, plus acceptance verification) are what "done"
means here.

---

## The mock environment

`scripts/generate_mock_alerts.py` seeds `appchi-v1` and `appchi-v2` from a seeded RNG on a
fixed clock, so the dataset is reproducible.

**A normal rerun appends another copy of every row.** Use `RESET=1` for a clean reload,
which deletes and recreates both indices with explicit mappings. The reset refuses any
endpoint that is not an explicit local mock, so a mistyped `ES_URL` cannot delete a real
index.

```bash
RESET=1 uv run python scripts/generate_mock_alerts.py
```

```bash
STATS_ONLY=1 uv run python scripts/generate_mock_alerts.py
```

Seven teams carry realistic data across the migration phases. Four `acceptance-*` teams
carry fixtures pinned to exact timestamps and exact expected outcomes; they exist so the
acceptance manifest can be computed by hand.

`scripts/es_scale_probe.py` is read-only and sizes the problem:

```bash
uv run python scripts/es_scale_probe.py --team checkout-api --run-at 2026-08-25T18:00:00Z
```

Its figures are approximate HyperLogLog++ cardinalities. The pipeline never uses them: it
pages every matching row and counts identities exactly, because a reported metric may not
be approximate.

---

## Architecture

```
src/alerts_bi/
  cli.py                 command line; a run always names one team
  versions.py            frozen ruleset / prompt / parser versions
  config/                environment configuration, split by what it configures
    env.py                 reading the environment and .env
    elasticsearch.py           sql.py                  } the three backing services
    llm.py                 /
    app.py                 the pipeline's configuration, composing those three
    api.py                 the HTTP surface: where it listens, what it writes
  api/                   HTTP trigger surface over the same pipeline
    app.py                 application factory and exception handlers
    routers/               the routes, grouped by what they are for
    service.py             everything that touches the pipeline, plus the run gate
    schemas.py             the wire contract; OpenAPI is generated from it
    dependencies.py        typed access to per-application state
    negotiation.py         the one rule for HTML versus JSON
    ui/                    the two pages the surface renders itself
    server.py              running it under uvicorn
  registry.py            ownership registry loading and validation
  es/                    Elasticsearch client and team-scoped reader
  domain/                run window, schema normalization, metric engine
  rules/                 R1-R4 and R7 core, R8-R10 readiness, aggregation, phase
  suppression/           panel SQL lexer, parser, field table, safety guards
  llm/                   grouping, request factoring, response validation, retry
  db/                    migrations, connection, repositories
  report/                HTML scorecard and the three CSV exports
  run/                   orchestrator, CLI command handlers, acceptance verification
scripts/                 mock seeder, scale probe, Kibana setup
docs/                    design, runtime flow, blueprint, alerting guides, fixture notes
tests/                   unit, integration and acceptance suites
test/fixtures/           the hand-authored acceptance oracle
```

`test/fixtures/` is deliberately not folded into `tests/`: the manifest is a reviewed
input to the acceptance check, not part of the suite that reads it, and its path is quoted
in the design and in the manifest's own header.

Each stage is independently testable, and the orchestrator invokes them in the order the
flow document fixes.

### Things worth knowing before changing anything

- **The window is exact and half-open.** `run_at` is captured once; the range is
  `[run_at - 168h, run_at)`. A mid-day run touches eight UTC dates, so the first and last
  daily buckets are partial and carry their real covered hours.
- **The run id is deterministic**, derived from team, window, registry hash and versions.
  Re-running the same team over a frozen `run_at` replaces its own rows rather than
  accumulating near-duplicates.
- **Core rules run on every raw row**, then aggregate to identity. Findings stay on the
  rows that matched and are never projected onto other rows or dates.
- **Any core finding anywhere in the window withholds the whole identity from the model.**
  V2 readiness gaps do not.
- **A batch gets three total attempts**, retried byte-for-byte as a whole. After the third
  failure every alert in it becomes `unassessed` with the shared reason. Alerts are never
  retried individually, and a partial response is never accepted.
- **Suppression resolves every ambiguity to `unmeasured`.** It feeds `flagged`, so a
  scoping predicate misread as suppression would mark good alerts bad — the most expensive
  error this design can make.

---

## Not in the MVP

Deliberately, and recorded in design section 7.4: any comparison between runs, a cross-team
leaderboard, R6 spam detection, historical backfill, the interactive frontend, the
company-wide unattributed-alert audit, panel discovery or live Grafana variable retrieval,
and a BI-side migration-invariant alert identity.

The approved next steps, in order: the interactive frontend over persisted runs, then
deterministic historical backfill oldest-first with no LLM calls.
