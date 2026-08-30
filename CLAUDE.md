# Alerts BI repository instructions

**Last updated:** 2026-08-30 (MVP in Python, merged to `main`)

## Mandatory first action

Before answering a repository question, planning, reviewing, running a command, editing a file, or delegating work, read [`alerts_bi_design.md`](docs/alerts_bi_design.md) **in full**.

Do this in every new session. Do not rely on chat history, summaries, or memory. Every subagent must also read the complete design document before starting its task.

If the design cannot be read, stop and report that blocker. It is the canonical product and architecture specification.

## Document authority and reading order

After reading the design, use these documents according to the task:

1. [`alerts_bi_flow.md`](docs/alerts_bi_flow.md) — concise runtime sequence for one MVP run.
2. [`alerts_bi_implementation_plan.md`](docs/alerts_bi_implementation_plan.md) — implementation components, milestones, tests, and definition of done.
3. [`Alerting_Guide_Appchi_EN.md`](docs/Alerting_Guide_Appchi_EN.md) and [`what_is_an_incorrect_alert_EN.md`](docs/what_is_an_incorrect_alert_EN.md) — the company standard. Read both completely for rule-engine, LLM-prompt, scoring, or alert-quality work.
4. [`team_alert_status.md`](docs/team_alert_status.md) — a description of the current synthetic fixture only. It predates the settled design and is **not** an acceptance oracle.

For any MVP implementation, architecture, integration, or acceptance task, read the runtime flow and implementation blueprint **in full** before changing code. For rule-engine, LLM-prompt, scoring, or alert-quality work, also read both alerting guides **in full**.

The precedence order is:

`alerts_bi_design.md` → approved flow → implementation blueprint → current code and mock documentation.

If a lower-priority document or existing implementation conflicts with the design, follow the design and reconcile the stale artifact. If new information would change an approved decision, explain the contradiction and obtain confirmation before editing the design.

## Implementation language

**Python is the approved implementation language** (decided 2026-08-30, superseding Node.js/JavaScript; design section 7.7). The change is implementation-only: every approved product behaviour, data contract, SQL schema, LLM contract and report contract is preserved exactly.

Required toolchain:

- Python 3.12 or later, defined by `pyproject.toml` with a **committed lockfile**.
- An installable `alerts_bi` package; type hints throughout application code.
- The official Elasticsearch Python client.
- SQLAlchemy Core over a real SQL Server driver (`mssql+pymssql`). Hand-written SQL executed as text; no ORM. Never SQLite and never an in-memory persistence substitute.
- The regular OpenAI **Python** SDK.
- `pytest` for tests, `ruff` for formatting and linting, `mypy` for strict static typing.

Keep production dependencies minimal. Nothing in the build, tests, mock seeding or runtime may require Node.js.

The superseded JavaScript implementation is preserved at the `javascript-mvp` tag. It was the behavioural reference for the port and is no longer in the tree. `test/fixtures/expected-results.json` was not regenerated for the port: it is the hand-authored oracle, and leaving it untouched is what let it detect a behavioural difference between the two implementations. Do not regenerate it now either.

The two implementations were compared row for row on the same fixture; the result and the two deliberate remaining differences are recorded in design section 7.7. Two behaviours are pinned by tests because the port could have changed them silently: the exact wording of every LLM principle and phase label (`tests/unit/test_catalog_text.py`), and the single instant format every hashed identifier depends on (`tests/unit/test_timefmt.py`). Do not "tidy" either without changing the version that describes it.

## Current repository state

The MVP is implemented in Python and merged to `main`; the port from JavaScript is complete and the JavaScript implementation has been removed. `rewrite/python` and `feature/http-api` are the branches it arrived on and are now superseded — do not treat either as current.

The tree holds `docs/` (design, runtime flow, blueprint, both alerting guides, fixture notes), the `alerts_bi` package under `src/` (including the `config` and `api` packages), SQL Server migrations and repositories, the versioned registry at `config/teams.json`, the Elasticsearch/Kibana/SQL Server mock stack, the Python mock and probe scripts under `scripts/`, the hand-authored oracle at `test/fixtures/expected-results.json`, and unit, integration and acceptance suites under `tests/`. The repository root holds only what tooling requires. `README.md` carries the operating instructions. Verify the current tree before relying on this statement.

The drift recorded in design section 7.5 was reconciled on 2026-08-30:

- The mock generator gained explicit index mappings, a guarded `RESET=1` clean reload, exact per-row timestamp control, and four appended `acceptance-*` teams covering the dense batching, over-200 group, R7 boundary, suppression-safety and blast-radius cases. It still generates post-MVP R6 and multi-month data, which is intended: the fixtures stay ready for R6 without re-seeding.
- The scale probe now reports `node_name_ratio` and `key_inflation_ratio` with their operands, scoped to one selected team, and states plainly that its figures are approximate.
- `team_alert_status.md` was rewritten against the settled phase and rule definitions.

Extend the existing generator; do not create a separate fixture system. `test/fixtures/expected-results.json` is the hand-authored acceptance oracle and must never be generated by the pipeline.

Boundaries settled during implementation are recorded in design section 7.6. Read them before changing rule, suppression, allocation or run-identity behaviour.

## Locked MVP scope

Keep these decisions intact unless the design is explicitly revised:

- Run manually for **one selected team**. Never default to all teams.
- Capture `run_at` once and query the exact UTC half-open window `[run_at - 168h, run_at)`.
- Validate the versioned team registry before querying. Require at least one source operator, enforce exact case-sensitive operator uniqueness across teams, and store the registry version, complete-file SHA-256, and selected-entry snapshot with the run.
- Query `appchi-v1` only by the selected team's configured v1 operators and `appchi-v2` only by its configured v2 operator. Elasticsearch is the sole alert source.
- Use `application + key_field` as the only alert identity. Do not use or persist an Elasticsearch row ID as business identity.
- Keep v1 and v2 volume separate. Publish distinct alerts as `sum(daily distinct identities) / 7`, labelled **distinct alerts per day**.
- Calculate node volatility from `node_name` through the approved `node_name_ratio`; use only nonempty-node rows in both numerator and denominator. Store diagnostic operands and return `null` for a zero denominator.
- Evaluate deterministic core rules on every raw row. Any core finding on an identity blocks that whole identity from the LLM, but findings stay attached only to rows that matched.
- Use the identity's most recent row as the representative document for LLM assessment and v2 readiness.
- Treat R8-R10 as phase-readiness gaps. They never block LLM assessment and do not enter deterministic quality totals.
- Derive the migration phase from identity presence and readiness; do not use self-reported phase or infer silent rule inventory.
- Persist each run and render reports only from committed SQL Server data.
- A local HTTP surface may start a run and return its scorecard (design section 7.8), built with FastAPI on uvicorn. It is a wrapper over the same `execute_run`/`persist_run` the CLI calls and adds no analysis: one team per run, `run_at` captured once, the same four files, reports rendered only from SQL, and runs serialized so two cannot race to write one deterministic `run_id`. It is not the deferred interactive frontend.
- Produce one self-contained HTML scorecard and exactly `daily_metrics.csv`, `rule_counts.csv`, and `alert_worklist.csv`.
- Report a single week without cross-run trends, deltas, baselines, leaderboards, or combined v1/v2 volume conclusions.

### Rule boundaries that commonly drift

- R1 and R2 use normalized **whole-value equality**, never substring or message-length matching.
- R3 checks required identity fields and an optional supplied `node_name`; an absent or empty optional `node_name` is valid.
- R4 applies only when `provider = grafana`. API alerts do not carry an alert-rule URL and never match R4 for its absence.
- R5 comes only from the approved panel-suppression evaluation.
- R6 spam detection is post-MVP.
- R7 applies only to v1. `time_created` is valid on both inclusive boundaries from `@timestamp - 24h` through `@timestamp`; future and older values are invalid.
- R8-R10 apply only to v2 and follow the exact catalogs and URL rules in the design.

### Suppression boundaries

- Parse supplied frozen panel SQL into an AST; never ask the LLM to interpret SQL.
- Panel SQL never establishes ownership and never narrows source alert counts.
- Evaluate only approved instance-field negations that are top-level `AND` leaves.
- Ignore and log unknown fields. Mark unsafe `OR` nesting, unresolved `query` variables, and missing required variable definitions as unmeasured.
- Resolve only the frozen supported variable types from the registry.
- Apply the greater-than-50% blast-radius guard and multi-panel unanimity rule.
- Cache the frozen interpretation by SQL-text hash and parser version.
- Do not call Grafana during an MVP run.

### LLM boundaries

- Use the regular OpenAI Python SDK against the compatible on-prem base URL.
- Use Chat Completions, strict JSON-schema output, temperature zero, configurable timeout, and the SDK's automatic retries disabled (`max_retries=0`).
- Use the deterministic fake client for normal tests. Live endpoint validation is separate and opt-in.
- Reuse durable verdicts by `(application, key_field, prompt_version, model_version)`. Store `classified_at`, the full representative document, its hash, and the verdict.
- Group candidates by `alert_rule_url`; when absent, group by `application`.
- Send one group per request and never pack groups together.
- Cap a request at 200 alerts. Split larger groups into deterministic balanced partitions after sorting; partition sizes may differ by at most one.
- Factor only fields that are identical across every alert in the batch. Reconstruction must be lossless.
- Persist the serialized request before calling the model. Retry the same complete batch byte-for-byte for **three total attempts**. Never retry individual alerts.
- Reject an invalid response as a whole. After the third failure, mark every batch member `unassessed` with the shared reason.
- Keep deterministic findings and LLM findings separate. LLM findings remain advisory.

## Post-MVP order

Do not expand the MVP with deferred features. The approved next steps are:

1. Design and build the interactive frontend over persisted runs and pipeline controls. The HTTP trigger surface of design section 7.8 already exists and is the seam it grows from; it is deliberately not that frontend.
2. Add deterministic historical backfill, oldest retained data first, with no LLM backfill.

Plan the unattributed-alert audit, cross-team leaderboard, R6, scheduling/Kubernetes, and other deferred work separately afterward.

## Local mock environment

[`docker-compose.yml`](docker-compose.yml) provides:

- Elasticsearch 8.15 at `http://localhost:9200`, container `alerts-bi-es`.
- Kibana 8.15 at `http://localhost:5601`, container `alerts-bi-kibana`.
- SQL Server 2022 at `localhost:1433`, container `alerts-bi-sqlserver`, pinned image with a health check.
- Persistent Elasticsearch data in the `es-data` volume.

Start the current services with:

```powershell
docker compose up -d
```

The mock indices are `appchi-v1` and `appchi-v2`. Kibana data views use `@timestamp` as their time field. This is plain Docker, not ECK; no Kubernetes cluster is available here.

Useful scripts:

- `scripts/generate_mock_alerts.py` seeds the synthetic dataset. A normal rerun **appends** another copy; `RESET=1` performs the clean reload the acceptance contract requires, and refuses any endpoint that is not an explicit local mock. `STATS_ONLY=1` computes statistics without writing.
- `scripts/es_scale_probe.py` is read-only and reports the two approved diagnostics with their operands, scoped to one selected team.
- `scripts/create_kibana_panels.py` creates the scale-probe dashboard and is rerunnable.

The generator's inputs live beside it: `scripts/mock_teams.json` holds the seven realistic teams, exported mechanically from the superseded JavaScript generator rather than retyped; `scripts/acceptance_teams.py` holds the four hand-authored `acceptance-*` teams; `scripts/_jsrandom.py` reproduces the JavaScript seeded RNG bit for bit, which is what keeps the dataset byte-stable across the port. `scripts/mock-data-stats.json` is the generator's committed summary of what it produced — regenerate it by running the generator, never by hand.

Reset data only when the task requires a clean fixture load. Before deleting indices or recreating a database, verify that the endpoint is the explicit local mock and that the target database is the disposable `alerts_bi_test`. Never apply destructive fixture operations to production or an unknown endpoint.

Compose already carries the pinned SQL Server 2022 service with its health check, and the persistent `alerts_bi_dev` and disposable `alerts_bi_test` databases exist with migrations applied. Credentials come from `.env`, which is never committed; `.env.example` carries placeholders. Do not substitute SQLite.

## Implementation and collaboration practices

- Inspect the repository and working-tree state before editing. Preserve unrelated and user-owned changes.
- Use Python 3.12+ with type hints, and reuse the existing mock scripts and request patterns where practical.
- Keep pipeline stages independently testable: registry, ES reader, normalization/metrics, deterministic rules, suppression, LLM, SQL persistence, and reporting.
- Configuration lives in `alerts_bi.config`, split by what it configures; the HTTP surface lives in `alerts_bi.api`, split by responsibility. Settings are configuration and belong in the former; runtime state belongs with the code that uses it.
- Establish shared contracts before parallel implementation.
- Use one primary integrator. Delegate only bounded tasks with disjoint file ownership; avoid independent sessions implementing competing architectures or editing the same files.
- Require each subagent to report assumptions, files changed, commands run, and test results. The primary agent reviews and integrates every contribution and runs the full suite.
- Use environment configuration for endpoints and credentials. Commit placeholders only; never commit secrets.
- Do not place alert documents, credentials, or complete LLM payloads in normal logs. Log identifiers, hashes, counts, timings, and redacted errors. Store audit payloads only in the approved SQL records.
- Prefer deterministic behavior: stable sorting, hashes, IDs, fixture clocks, and output ordering.
- Use the existing mock ES and a disposable SQL Server database for integration tests. Do not replace them with invented in-memory integration substitutes.

## Verification and completion

Implementation is not complete until the relevant unit, integration, and acceptance checks pass. Verify at least:

- Exact paginated ES retrieval for one selected team.
- Window boundaries, partial UTC buckets, daily rollups, identity selection, and diagnostic operands.
- Every deterministic rule boundary, especially R4 provider scoping and R7 time limits.
- Suppression AST safety, variable behavior, unanimity, and blast-radius handling.
- Stable LLM grouping, balanced partitions, lossless factoring, exact response-ID validation, byte-identical three-attempt retries, batch-wide failure, and durable verdict reuse.
- SQL Server migrations, constraints, transactions, restart/idempotency behavior, and SQL-only report rendering.
- Deterministic CSV ordering, formula-injection protection, HTML escaping, and the exact output file contract.
- Reconciliation against a hand-reviewed `test/fixtures/expected-results.json` that the production pipeline does not generate.
- For the HTTP surface: that it refuses a run with no team, an unknown team, an unknown model mode and a second concurrent run; that only the four approved outputs are addressable; and that a scorecard it serves is byte-identical to the one the CLI writes for the same run.

Run formatting, linting, type checks, unit tests, integration tests, migrations, and a clean mock acceptance run when those commands exist. Report commands and results accurately. Never claim live LLM, production Elasticsearch, or full acceptance validation unless it ran successfully.

## Keep project instructions synchronized

[`AGENTS.md`](AGENTS.md) and [`CLAUDE.md`](CLAUDE.md) must remain behaviorally equivalent. When project-wide guidance changes, update both files in the same change.

When a product or architecture decision is made, update `alerts_bi_design.md` in the same session, move resolved questions into the relevant section, and update its `Last updated` date. Update the flow, blueprint, fixture documentation, and expected-results manifest when the decision changes their behavior.
