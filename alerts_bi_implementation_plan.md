# Alerts BI — Implementation Blueprint

This document defines what to build and the recommended order of implementation. [`alerts_bi_design.md`](alerts_bi_design.md) remains the source of truth when this blueprint and the design differ.

## 1. MVP deliverables

Implement a runnable tool that:

1. Accepts one team as input and analyzes its last seven days of alerts.
2. Reads that team's v1 and v2 alerts from Elasticsearch.
3. Calculates daily volume, quality, visibility, and migration metrics.
4. Applies the approved deterministic and suppression rules.
5. Uses durable verdict reuse and the approved grouped LLM batching flow.
6. Persists reproducible run data in SQL Server.
7. Produces one HTML team scorecard plus CSV exports.

The MVP does not include scheduled execution, historical backfill, an interactive frontend, cross-team ranking, a company-wide unattributed-alert audit, R6 spam detection, or automatic enforcement of LLM findings.

## 2. Recommended implementation shape

Use a Node.js ESM command-line application for the MVP. This matches the existing mock scripts and allows their Elasticsearch request code and fixtures to be reused. Keep the components independent so a scheduler or service wrapper can be added later without changing the analysis logic.

### 2.1 Run orchestrator

Responsibilities:

- Parse the selected team and optional run-time overrides.
- Create the run ID and freeze the seven-day window and all versions.
- Invoke each stage in order.
- Record stage status, failures, and final run status.
- Allow safe restart without duplicating a completed run.

The orchestrator must never default to all teams.

### 2.2 Team registry

Create `config/teams.json` and a checked-in JSON Schema. The versioned registry contains:

- Stable team ID and display name.
- V1 operator list.
- V2 operator.
- Optional Grafana panel-query definitions.
- Registry version and effective date.

Allow an empty v1 operator list for migrated teams and a nullable v2 operator for pre-migration teams, but require at least one source operator. Validate exact case-sensitive operator uniqueness across teams and missing required fields before a run starts. Store the declared version, complete-file SHA-256, and selected team entry as an immutable run snapshot. Include a mock registry populated from the existing generator's teams.

### 2.3 Elasticsearch reader

Implement separate v1 and v2 queries using the selected team's operators and the exact run window. Support pagination so result size does not change correctness.

Capture `run_at` once, use UTC throughout, and query the exact half-open interval from `run_at - 168 hours` through but excluding `run_at`. Assign rows to UTC calendar dates and persist exact boundaries and covered hours for partial first and last buckets.

The reader should return normalized records while retaining the complete source document for representative selection and LLM input. Reported distinct counts must be exact; use composite aggregation or exact counting over the paged result instead of relying on approximate cardinality.

### 2.4 Normalization and metric engine

Implement schema adapters that map v1 and v2 documents into a common internal record without erasing schema-specific fields.

The engine must:

- Build identity from `application + key_field`.
- Retain all rows for volume metrics.
- Choose the latest row as the distinct alert's representative.
- Evaluate core rules on every raw row before aggregating findings by identity.
- Produce daily metrics and seven-day rollups.
- Calculate `node_name_ratio` and `key_inflation_ratio` exactly as defined.
- Keep v1 and v2 metrics separately addressable.

Use the approved rollups exactly: bucket row count; bucket distinct identity count; bucket rows divided by covered hours; 168-hour row total; total rows divided by 168; and `sum(bucket distinct counts) / 7` for the published distinct-alert daily rate. Do not replace the last calculation with the distinct identity count across the complete window, and never merge v1 and v2 row counts for migration conclusions.

Persist both numerator and denominator for each diagnostic ratio. Use only nonempty-node rows on both sides of `node_name_ratio`, all rows for `key_inflation_ratio`, `null` for a zero denominator, and `sum(daily numerators) / sum(daily denominators)` for the scorecard rollup.

### 2.5 Deterministic rule engine

Represent every rule as a versioned, independently testable function returning a finding code and evidence.

Implement R1, R2, R3, R4, R5, and R7 as core findings. Implement R8–R10 as phase-2 readiness gaps. The engine must support multiple findings per alert and separately calculate the union of core-flagged alerts.

For each core rule, persist the raw-row match count and the count of distinct identities with at least one matching row. Do not project a finding onto non-matching rows under the same identity. If any row under an identity has a core finding, exclude that entire identity from LLM assessment; otherwise use its most recent row as the representative document.

Allocate deterministic counts only to buckets containing actual matching rows. Project an LLM verdict across its identity: high-confidence flagged identities contribute all of their raw rows to `flagged_by_llm`, and all LLM terminal states contribute the identity once to every bucket where it appears. Sum row counts for the scorecard; divide the sum of every daily distinct-identity count by seven and label it as a per-day rate. Keep the worklist at one row per identity.

Pay special attention to these boundaries:

- R1 normalizes by trimming, lowercasing, collapsing whitespace, and removing surrounding punctuation, then performs a whole-message match against the versioned catalogue: `error occurred`, `something went wrong`, `unable to get data`, `alert triggered`, and `issue detected`. It never flags on length alone or on substring matches.
- R2 uses the same normalization and whole-message matching against: `i am alive`, `ok`, `healthy`, `started`, `completed`, `running`, `service started`, `process running`, and `completed successfully`. It never uses substring matching.
- R3 checks `application`, `operator`, `object` / `component`, and a supplied `node_name`. After trim/lowercase/whitespace normalization, exact values `unknown`, `test`, `default`, and `n/a` match. Empty values match only on the required fields; an absent or empty `node_name` is valid.
- R4 requires both `provider = grafana` and a missing rule URL.
- R7 applies to v1 and accepts both endpoints of the 24-hour interval.
- R8 applies only to v2 and matches a missing, `null`, non-string, empty, or exact-placeholder `impact`. It is a readiness gap, not a core finding, and never blocks the LLM.
- R9 applies to every v2 severity and requires a non-placeholder, valid absolute HTTP(S) URL. It remains a readiness gap and never blocks the LLM; only a critical R9 match prevents phase completion.
- R10 normalizes `impact` like R1 and matches only the complete values `high cpu`, `high cpu usage`, `cpu usage is high`, and `cpu is high`. Broader semantic matching belongs to LLM principle P9.
- R8–R10 never block the LLM.

### 2.6 Panel-suppression evaluator

Build a small parser for the supported query language rather than interpreting query text with string matching. Its output should include:

- Parsed AST or parse failure.
- Resolved variables and unresolved query variables.
- Safe exclusion predicates.
- Per-panel decision and multi-panel consensus.
- Blast-radius result.
- Final state: suppressed, not suppressed, or unmeasured.

Cache the result by SQL-text hash and parser version. A parse or safety failure must produce `suppression_unmeasured`, never an assumed suppression.

### 2.7 LLM assessment module

Split this module into five testable parts:

1. **Verdict lookup:** read by application, key, prompt version, and model version.
2. **Grouping and partitioning:** rule URL first, application fallback, one group per request, maximum 200, deterministic balanced partitions.
3. **Prompt construction:** include the full guides and P1–P11 catalogue in the cached prefix; construct the approved request object with complete reconstructable representative documents, deterministic transport IDs, and only proven-identical shared fields. Persist the serialized payload before the first attempt and reuse it byte-for-byte for retries.
4. **Response validation:** enforce the closed output schema, echoed batch ID, exact alert-ID set, enum relationships, one primary principle per alert, nonempty justification up to 1,000 characters, and no additional fields.
5. **Retry and persistence:** store every batch attempt; retry the identical whole batch; stop after three total attempts; save successful verdicts durably or mark the failed batch unassessed.

The prompt and model identifiers must be explicit versions, not free-form labels changed in place.

The prompt must encode the approved per-alert decision procedure and confidence meanings. Treat changes to the instructions, guides, or R/P catalogue as a prompt-version change, and test that batch neighbours provide context without causing verdict copying.

Use the regular OpenAI Node SDK with the on-prem `baseURL`, API key, and model/deployment identifier supplied through configuration. Call Chat Completions with strict JSON-schema output and temperature zero. Set SDK automatic retries to zero so the pipeline alone enforces and audits the three total attempts; make the per-attempt timeout configurable and count transport failures and timeouts as attempts. Lock the SDK version in the package lockfile and provide a deterministic fake client for tests.

The fake implements the same `LlmClient` contract and selects scripted results by batch ID and attempt number. Cover valid responses, transport failures, timeouts, malformed JSON, batch-ID mismatch, every alert-ID set failure, invalid enum relationships, recovery on a later attempt, and total exhaustion. Unit and integration tests must prove byte-identical retries, exactly three attempts, whole-batch rejection, batch-wide unassessed state, persistence, and reuse. Keep live endpoint tests opt-in.

### 2.8 SQL Server persistence

Create migrations and repositories for these logical tables:

- `runs`: one row per execution, including the team, time window, version set, registry-file hash and selected-entry snapshot, phase, readiness, and LLM assessment status.
- `daily_metrics`: one row per run, team, schema, and snapshot date with volume, quality, and visibility values, including the distinct-identity `needs_review` count.
- `daily_rule_counts`: per-rule daily row and distinct-alert counts.
- `alert_findings`: identity, representative metadata, deterministic findings, LLM outcome, readiness gaps, evidence, and worklist state.
- `llm_batch_attempts`: group key, partition, attempt number, request hash, response status, validation error, and timing.
- `llm_verdicts`: durable cache key, verdict, representative document, document hash, and classification time.
- `panel_parses`: SQL-text hash, parser version, parsed result, and safety state.

Add uniqueness constraints around natural idempotency keys, especially run identity, daily grain, verdict cache key, batch attempt, and panel-parse key. Persist complete JSON payloads where auditability requires the original structured document, but keep fields used for filtering and reporting in typed columns.

### 2.9 Report renderer

Render reports only from persisted data. Provide:

- A self-contained HTML team scorecard.
- Exactly `daily_metrics.csv`, `rule_counts.csv`, and `alert_worklist.csv`.
- Clear separation between the mutually exclusive identity states `rule_flagged`, `llm_flagged`, `needs_review`, `assessed_good`, and `unassessed`, with readiness gaps kept orthogonal.
- Visible run metadata and version identifiers so results can be reproduced.

The HTML contains run metadata; migration; schema-separated volume and diagnostics; quality; visibility and supplied panel IDs; rule/principle breakdown; actionable work list; and limitations. It contains no cross-run trend, delta, leaderboard, or combined v1/v2 volume conclusion. Use deterministic ordering, retain full CSV values, HTML-escape every source value, and neutralize cells beginning with `=`, `+`, `-`, or `@` against spreadsheet formula injection. Defer the interactive frontend until after the MVP.

## 3. Reconcile the existing mock before feature work

The mock environment is the implementation test bed, but parts of it predate the approved flow. Update it before treating its expected results as acceptance data:

- Change the scale probe from the old node-inflation measure to `node_name_ratio` and `key_inflation_ratio`, with a selected-team operator filter.
- Add R7 fixtures for both future `time_created` and values older than 24 hours; keep boundary-valid examples.
- Confirm API alerts without an alert-rule URL do not trigger R4.
- Add dense alert-rule-URL groups, missing-URL application groups, and groups above 200 for batching tests.
- Add safe, unsafe, unresolved-variable, multi-panel-disagreement, and over-50%-blast-radius suppression cases.
- Stop using the mock's Unattributed bucket as part of the MVP run path.
- Update `team_alert_status.md` after fixture behavior changes.

Do not create a second fixture system; extend `scripts/generate-mock-alerts.mjs` and continue using the existing Elasticsearch and Kibana containers.

## 4. Delivery milestones

### Milestone 1 — Prove the risky boundaries

Build small executable probes for:

- Exact, paginated retrieval for one high-volume team.
- Exact distinct and ratio calculations.
- LLM structured output for representative batches.
- Large-group balanced partitioning and three-attempt group retry.
- Factored versus unfactored prompts on known difficult alerts.

Success means the ES path completes within an agreed run budget and the LLM contract produces valid, attributable outputs without losing alert context.

### Milestone 2 — Deterministic vertical slice

Implement the CLI, registry, ES readers, normalization, daily metric engine, deterministic rules except R5, SQL migrations, and a basic stored-data report.

Success means one command runs a selected mock team end to end without any LLM call, persists reproducible daily results, and matches hand-checked fixture counts.

### Milestone 3 — Suppression analysis

Implement the panel parser, variable handling, safety rules, multi-panel consensus, blast-radius guard, parse cache, R5 integration, and visibility metrics.

Success means every suppression fixture produces the expected suppressed, not-suppressed, or unmeasured state with evidence.

### Milestone 4 — LLM assessment

Implement verdict reuse, grouping, balanced partitions, prompt construction, response validation, three-attempt whole-batch retry, durable verdict storage, and worklist outcomes.

Success means rerunning the same prompt/model version reuses prior verdicts, invalid responses never produce partial findings, and exhausted batches are fully marked unassessed.

### Milestone 5 — Complete scorecard and hardening

Finish the HTML and CSV outputs, phase derivation, readiness display, operational logging, idempotent reruns, failure recovery, and performance tests.

Success means every mock team can be run individually and its stored scorecard reconciles with source rows and acceptance fixtures.

## 5. Test requirements

### Unit tests

Cover at least:

- Identity construction and representative selection.
- UTC half-open window boundaries, partial daily buckets, and seven-day rollups.
- Both diagnostic ratios and empty-node handling.
- Every deterministic rule, including R4 provider scoping and all R7 time boundaries.
- Core-finding short circuit versus readiness-gap pass-through.
- Suppression AST safety and variable behavior.
- Group fallback, maximum size, deterministic sorting, and balanced partition sizes.
- Shared-field factoring only when values are identical.
- Exact response-ID validation and whole-batch retry exhaustion.
- Phase derivation.

Compute `phase2_readiness_pct` from the most recent representative of each distinct v2 identity. Readiness requires no R8 or R10 and, for critical alerts only, no R9. Divide by all distinct v2 identities and store `null` when the denominator is zero; continue exposing non-critical R9 gaps separately.

Derive phase exhaustively from distinct identity presence: neither schema → `no_data`; v1 only → `phase_0`; both → `phase_1`; v2 only below 100% readiness → `phase_2`; v2 only at 100% → `done`. Test every branch and do not infer inventory that did not fire in the run window.

### Integration tests

Run against the existing mock Elasticsearch instance and a disposable SQL Server database. Verify pagination, stored constraints, rerun idempotency, verdict reuse, and report queries.

Extend `docker-compose.yml` with a pinned SQL Server 2022 container, readiness health check, persistent development volume, persistent `alerts_bi_dev` database, and disposable `alerts_bi_test` database. Load credentials from an uncommitted `.env` documented by `.env.example`. Provide commands to apply and inspect migrations and to recreate only the explicitly named test database. Production uses the same migrations with an external connection string; do not silently replace SQL Server with a different persistence engine.

### Acceptance tests

Give the existing seeded generator a fixed default acceptance clock and require a clean reload. Reconcile every approved rule, add dense and over-200 batching groups, and cover every suppression and retry path. Maintain a hand-reviewed `test/fixtures/expected-results.json` for every mock team with daily volume, diagnostic operands, per-rule counts, batch membership, quality states, suppression, readiness, and phase. The production pipeline must not generate this oracle. Provide a verification command that compares persisted SQL results and CSV exports with it; check HTML structure and required content rather than volatile formatting.

## 6. Configuration and security

Keep configuration outside source code. The runtime needs:

- Elasticsearch URL and credentials.
- SQL Server connection details.
- LLM endpoint credentials, deployment/model version, and prompt version.
- Supplied panel-query text and frozen custom, constant, interval, and multi-value definitions. The MVP neither discovers panels nor retrieves variables through the Grafana API; query variables and missing required definitions become unmeasured.
- Registry and ruleset versions.

Never place alert data, credentials, or full LLM payloads in ordinary logs. Log run IDs, batch IDs, hashes, counts, durations, and redacted error summaries. Store auditable payloads only in the approved SQL records with the required access controls.

## 7. Failure behavior

- **Registry invalid:** fail before querying alerts.
- **One ES query fails:** fail the run; do not publish a partial team scorecard as complete.
- **One alert is malformed:** record the validation problem and apply the designed unassessed/invalid path; do not abort unrelated alerts unless identity or ownership cannot be established.
- **Panel query unsafe or unresolved:** mark suppression unmeasured and continue.
- **One LLM batch exhausts three attempts:** mark that complete batch unassessed and continue the run.
- **SQL persistence fails:** fail the run and do not render a report from in-memory results.
- **Report rendering fails after persistence:** retain the completed analysis and allow rendering to be retried from SQL Server.

## 8. MVP definition of done

The MVP is complete when:

- A user can select any registered team and run the last-seven-day analysis.
- Queries retrieve only that team's configured operators.
- Stored daily metrics reconcile with mock source data.
- All rule, suppression, batching, retry, and phase edge cases pass.
- Repeated runs are auditable and do not corrupt durable caches.
- The HTML scorecard and CSV exports are generated solely from SQL Server.
- LLM results are visibly advisory and versioned.
- Deferred features are absent from the MVP execution path.

## 9. Managed follow-up after the MVP

Build the interactive frontend first, using the persisted run data and pipeline controls; define its detailed product scope after the MVP rather than expanding the current build.

Implement deterministic historical backfill second. Process the oldest period first, reuse the same registry/ruleset versioning and persistence grain, and make no LLM calls. Track completion so ranges can be resumed safely.

After backfill, separately plan the company-wide unattributed-alert audit, cross-team leaderboard, R6 spam analysis, and any scheduled or Kubernetes deployment work.
