# Alerts BI

Measures one team's alerting for one week and hands that team a concrete list of what to
fix.

A run selects **one** team, reads its last 168 hours from Elasticsearch, applies the
deterministic rule set, measures how much of its own inventory the team hides from its
dashboards, asks an on-prem model about everything the rules could not decide, persists the
result to SQL Server, and renders a scorecard from the stored rows.

**The tool reports numbers; people draw conclusions.** Every figure in the scorecard is a
statement about a single week. There is no comparison against a previous run, no trend, no
baseline and no cross-team leaderboard — by design. The read-only
[review portal](#the-review-portal) shows each team's published weeks over time, still
without deltas or conclusions.

[`docs/alerts_bi_design.md`](docs/alerts_bi_design.md) is the canonical specification.
[`docs/outputs.md`](docs/outputs.md) explains what a run emits, and
[`docs/openshift-deployment.md`](docs/openshift-deployment.md) covers running it on a cluster.
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
| `alerts-bi db status` | Show the current revision and whether each migration still matches its checksum |
| `alerts-bi db reset-test` | Drop and recreate **only** the configured disposable test database |
| `alerts-bi verify-acceptance` | Compare persisted rows and CSVs against the hand-reviewed manifest |
| `alerts-bi serve` | Serve the HTTP trigger surface (see below) |
| `alerts-bi portal` | Serve the read-only review portal (see below) |
| `alerts-bi publish`, `unpublish`, `publications` | Publish a completed run as a team's weekly review, withdraw one, list them |
| `alerts-bi decide`, `decisions` | Record and list human decisions on findings |
| `alerts-bi db grant-reader` | Optional legacy utility to create a restricted login for direct access to portal views |
| `alerts-bi weekly` | Run and publish every due Monday week of every enrolled team |
| `alerts-bi weekly-status` | Each enrolled team's latest published week and last schedule outcome |
| `alerts-bi registry check` | Validate the team registry before deploying an edit |
| `alerts-bi db setup` | Apply pending migrations; idempotent, for an init container |
| `alerts-bi admin` | Serve the operator admin app on loopback, behind a login proxy |

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

## Automatic weekly reviews

Enrolled teams are reviewed and published every week without anyone running them (design
section 7.11). Every team's week is **Monday 00:00 UTC to Monday 00:00 UTC**.

### Adding a team

1. Add its entry to `config/teams.json` - operators, optional panels - with
   `"weekly_review": { "enabled": true }`, and bump `registry_version`.
2. Validate the file:

```bash
uv run alerts-bi registry check
```

3. Deploy it. The next scheduled run reviews the team's most recent completed week and
   publishes it; from then on every week follows. Earlier weeks are not backfilled.

### What runs

```bash
uv run alerts-bi weekly
```

Run it as often as you like - on OpenShift a CronJob runs it daily. It only does what is due:

- each enrolled team's completed Monday weeks since its latest published one, oldest first;
- a week is **published** automatically when it is healthy (the model assessed every alert);
- an unhealthy week is **held** and retried every day; the healthy weeks after it are run and
  **stored**. If it is still unhealthy three days after it was first held, it is **published
  anyway** with a note to readers saying how many alerts the automated review could not assess;
- a week older than 84 days is **expired** - its data is past retention - and the next week is
  published across the gap;
- a team whose published history is not on the Monday boundary is **blocked** until you align
  it.

It exits non-zero whenever something needs a person. See where every team stands:

```bash
uv run alerts-bi weekly-status
```

Resolve a held week by publishing its run yourself after checking it, or skip it by
publishing the next week with `--allow-gap`. `--dry-run` shows what is due without running
anything, `--team` narrows to enrolled teams, and `--as-of` fixes "now" for the mock:

```bash
uv run alerts-bi weekly --as-of 2026-08-25T18:00:00Z --fake-llm
```

---

## The operator admin app

A web screen for the standardization team, so nothing needs a command on a pod (design
section 7.12). It lists every team with its schedule status, every run with its publication
state and full scorecard, and a published week's findings. It can publish a run, withdraw a
week, and record decisions - each recorded under the signed-in person's name.

It has no login of its own: in OpenShift it sits behind the `oauth-proxy` sidecar, which signs
people in, admits only the standardization team, and passes their name in
`X-Forwarded-User`. It binds to loopback only, so the proxy is the only way in. It needs
`ADMIN_SECRET` (32+ characters), which signs its forms.

Locally, without a proxy:

```bash
ADMIN_SECRET=local-development-secret-0123456789 uv run alerts-bi admin --dev-user yourname
```

Then open `http://127.0.0.1:8200`. `--dev-user` acts as that name for every request; never
use it anywhere shared.

### Summary pages

`GET /teams/{team_id}/summary` is the team summary for any completed run, internals included
(design section 7.14): volume and rule-flagged tiles per schema, model coverage, phase, why
alerts were flagged, key findings, noisy alerts by application, how often alerts fire, the
biggest single source, the per-rule table, hidden and `unseen` alerts, migration progress,
the estimated time to retire v1, and a filterable work list. v1 and v2 are never summed.
The reader portal shows published weeks only, split into tabs (see below), under the portal's
rules (weekly totals, no run id or version). Its Volume and Migration tabs leave out the
per-application table and the phase-1 estimate; both stay on this admin page and on the
portal's presentation slides.

---

## The review portal

A separate, **read-only** web surface where anyone on the company network can see every
team's published weekly reviews, follow them over time, and open individual alerts
(design section 7.10). It has no login, and it cannot start runs, publish, record decisions
or change anything.

Four things are kept apart:

| | Who | Visible in the portal |
|---|---|---|
| **Run completed** | the pipeline | never - runs are operator-facing |
| **Review published** | an operator, `alerts-bi publish` | yes; only published weeks exist there |
| **Machine finding** | the rules and the advisory model | yes, with its stored evidence |
| **Human decision** | an operator, `alerts-bi decide` | yes, as a history beside the finding |

### Setting it up locally

The portal uses the same `SQL_HOST`, `SQL_PORT`, `SQL_USER`, `SQL_PASSWORD` and
`SQL_DATABASE` as the pipeline. It queries the `portal_*` views (`portal_reviews`,
`portal_schema_totals`, `portal_alerts`, `portal_decisions`, `portal_rule_totals` and
`portal_daily_metrics`). Apply the migrations, through `008`, if the database-owning team
has not already done so. An existing installation upgrading to the team summary needs
`005_team_summary` (summary columns and views), `006_r6_episodes` (R6 episode facts),
`007_portal_daily` (the day-by-day view) and `008` (`basis_changed` compares the team's own
registry entry, not the whole-file registry version):

```bash
uv run alerts-bi db migrate
```

`005_team_summary` uses `STRING_SPLIT`, so the database's compatibility level must be 130
(SQL Server 2016) or higher. Check it before applying:

```sql
SELECT compatibility_level FROM sys.databases WHERE name = DB_NAME();
```

Run a team, then publish that run as its weekly review:

```bash
uv run alerts-bi run --team notifications-svc --run-at 2026-08-25T18:00:00Z --fake-llm
```

```bash
uv run alerts-bi publish --run-id <run id printed above> --note "First review"
```

Start the portal and open `http://127.0.0.1:8100`:

```bash
uv run alerts-bi portal
```

The portal does not inspect the SQL login's permissions at startup. The configured login
must be able to read the `portal_*` views for pages to load.

### Operator commands

These run with the owning credential (`SQL_USER`) and are the only way to publish or decide.

| Command | What it does |
|---|---|
| `alerts-bi publish --run-id <id> [--note ...]` | Publish a completed run as its team's weekly review |
| `alerts-bi publish ... --replace` | Publish in place of the run already published for exactly that week; the earlier publication is withdrawn, not deleted |
| `alerts-bi publish ... --allow-gap` | Publish a week that is not adjacent to the team's published weeks |
| `alerts-bi unpublish --run-id <id> --reason ...` | Withdraw a published week; readers stop seeing it |
| `alerts-bi publications --team <id>` | List a team's publications, current and withdrawn |
| `alerts-bi decide --team <id> --week YYYY-MM-DD --schema v1 --application <a> --key-field <k> --finding R1 --state confirmed --note ...` | Append a human decision on one finding |
| `alerts-bi decisions --team <id>` | List a team's decision history |
| `alerts-bi db grant-reader` | Optional legacy utility to create a restricted view login; the portal does not use it |

Publishing refuses a week that overlaps a published one, always. Weeks are meant to be back
to back: run each team with `--run-at` set to the end of its previous published week. A
decision is recorded against a published week and keyed on the exact alert identity, so it
never carries over to the new v2 key a team mints by enriching an alert. Decisions are
append-only; a changed mind is a new decision.

A run that is currently published cannot be re-persisted underneath its readers: `run`
refuses, and the week has to be withdrawn first.

### What readers see

- A **team directory** with each team's latest published week, listed alphabetically.
- For each team, a **week menu** (plain links, so it keeps the open tab) and the week in
  **seven tabs**, each its own address under `/teams/{team}/weeks/{week}`:
  - **Overview** (the week's own address): for v1 and v2 separately the alerts and events of
    the week and what the review found, the key findings, the phase, and the loudest alert.
    These are weekly totals, not the scorecard's per-day rate, and v1 and v2 are never added
    together.
  - **Fix list** (`/fix`): what to change, grouped as fix or delete, advisory and get v2
    ready, with each schema's alerts side by side; then every alert, paginated and
    filtered by schema, outcome and problem. Opening an alert shows each finding with its
    stored evidence (an older matching event is labelled apart from the latest one), advisory
    model findings with their original reasoning, the decision a person has to make for an
    uncertain one, v2 readiness in its own section, and the decision history.
  - **Volume** (`/volume`): the loudest alerts per schema and any stuck, spamming or flapping
    pattern.
  - **Dashboards** (`/dashboards`): alerts your own panels hide, and alerts on no dashboard.
  - **Migration** (`/migration`): the phase, what is left in v1, phase-2 readiness and the
    critical alerts without a runbook.
  - **History** (`/history`): one point per published week, one chart per schema and measure,
    and the list of published weeks. No deltas, percentages or "fixed" labels.
  - **Slides** (`/slides`): two presentation slides for the week, with a day-by-day chart of
    distinct and rule-flagged distinct alerts labelled "by UTC day" (a within-week view,
    never a comparison across weeks).
- Problems are named in plain words ("No rule link", "Generic message"); rule ids are not
  shown. Links from before the tabs, such as `/teams/{team}?rule=R1`, redirect to the Fix list.

It never shows a run id, registry, ruleset, prompt or model version. The scorecard keeps
those.

### Network exposure

The portal binds to `127.0.0.1:8100` by default (`PORTAL_HOST`, `PORTAL_PORT`). It admits
only clients on `PORTAL_ALLOWED_NETWORKS` - loopback and the private address ranges by
default - and answers anyone else with `403`. Behind a reverse proxy the client address is
the proxy's, so narrow the allowlist to the proxy there. Every page carries a
Content-Security-Policy that forbids script, framing and inline styles; the pages contain no
script at all.

Never mount the trigger surface of `alerts-bi serve` on the portal's listener: it is a
separate application with an unauthenticated write endpoint.

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

[`docs/outputs.md`](docs/outputs.md) documents all four: the scorecard section by section,
every column of every CSV, the API's JSON shapes, and what the outputs deliberately do not
say. The essentials are below.

### Reading the numbers

Two counts always travel together. `alerts` is the raw row count — pipeline and dashboard
load. `distinct_alerts` is the count of distinct `application + key_field` identities — how
many things actually fired. One stuck v1 alert is roughly 288 rows a day and one distinct
alert; a team genuinely flooding the pipeline looks completely different. A team needs the
first number to care and the second to act.

**Every distinct figure is published as a per-day rate**, never as a window total, because
a 7-day total is 7× a 1-day total for arithmetic reasons alone.

**v1 and v2 row counts are never added together.** Grafana writes a row on every
evaluation of a firing rule, and the evaluation cadence differs between the two schemas, so
the same alert yields a very different row count in each. Moving one alert between schemas
therefore changes its row count without anyone improving anything.

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
| `PORTAL_HOST`, `PORTAL_PORT` | Where `alerts-bi portal` listens; default `127.0.0.1:8100` |
| `PORTAL_ALLOWED_NETWORKS` | Comma-separated client networks the portal admits; default loopback and private ranges |
| `PORTAL_PAGE_SIZE` | Work-list page size; the portal reads `SQL_DATABASE` as `SQL_USER` |

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

Runs the six acceptance teams and compares the persisted SQL rows and the rendered CSV
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

### Evaluating the LLM review upgrade

Prompt `1.2.0` added scope-aware evidence guidance and checks for inapplicable citations. It is
a candidate for live quality evaluation; passing protocol tests does not establish better
judgment. The current prompt is `1.3.0` (ruleset `1.1.0`), which carries the same guidance
plus the R6 catalogue line. The [upgrade plan](docs/llm_review_upgrade_plan.md) records release gates and
[design section 7.13](docs/alerts_bi_design.md#713-llm-review-quality-evaluation-and-durable-audit)
specifies audit/recovery behavior.

Apply migration `004_llm_review_audit` (and the later `005_team_summary`, `006_r6_episodes`,
`007_portal_daily` and `008`, which changes `portal_reviews.basis_changed` to compare the
team's own registry entry) through the normal `alerts-bi db migrate` command
before running the upgraded live pipeline (the deployment init container runs setup).
It stores requests before calls, preserves successful responses across interrupted runs,
and records uncertain interrupted attempts against the three-attempt budget. A later explicit
retry after exhaustion gets a new recorded cycle. The four report files and portal API stay
unchanged. Audit tables are operator-only and retain full documents; use existing SQL access
and backup controls, not ordinary logs, for review evidence.

Run the isolated benchmark without calling any model:

```powershell
uv run python scripts/evaluate_llm.py --mode fake --caps 1 10 --repeats 2 --out out/evaluations/smoke.json
```

It defaults to development cases, both full and factored documents, and normal/reversed
within-partition ordering. Supported caps are 1 through 200; default trials use
1/10/25/50/100/200. The checked-in 14-case corpus is draft, synthetic and small. A reported
`max_actual_batch=2` does not validate capacity at 200. The default all-good fake exercises
the harness and deliberately misses labelled violations; its precision is undefined.
Inspect per-group uncertainty and subgroup counts, not just an overall score. Explanations
still need blinded human review; summaries do not grade the truth of free text.

For an exploratory on-prem trial, configure the existing endpoint credentials, a stable
`LLM_MODEL_REVISION` when `LLM_MODEL` is a mutable alias, and an already migrated audit database:

```powershell
$env:LLM_LIVE_TEST = 'true'
uv run python scripts/evaluate_llm.py --mode live --database alerts_bi_dev --caps 1 --allow-draft
```

The explicit `--allow-draft` is needed until independent reviewers adjudicate the labels.
Evaluation scopes never read or write the production verdict cache or publish a run.
The command stops the matrix after a failed attempt unless `--continue-after-failure` is
explicitly supplied. Each completed trial saves a summary; raw requests/responses stay in
the SQL audit. No result automatically enables a model or satisfies release gates.

Use `--cases` for a separately reviewed manifest, keep families and actual groups within one
split, freeze development decisions, then use `--split holdout`. Each case supplies either a
synthetic `definition` (see the checked-in manifest) or a complete frozen `source` representative,
which is preserved exactly. Keep private case manifests outside Git. Annotations carry accepted
`principles`, `confidences`, `evidence_fields` and a `rationale`; `review_state=reviewed` requires
named `reviewers`. Include independently sampled `no_violation` cases to detect misses; confirm/
dismiss clicks alone cover only findings and are not automatically gold labels.
To compare a saved baseline
prompt, pass its UTF-8 file with `--system-prompt` and its distinct `--prompt-version`; compare
summary files with `--compare`. This keeps the current strict validator, so this comparison
isolates prompt/model changes rather than reproducing the old implementation's weaker
validator. Reproducing that old pipeline requires its matching source revision.

`LLM_MAX_COMPLETION_TOKENS=0` preserves omission of that SDK parameter. Only set a positive
value after testing support and capacity on the endpoint. Usage/cached-token fields stay null
when unavailable. Review baseline/candidate explanations and large/long representative groups
before selecting a production cap or deployment; keep the prior release/model available for
rollback of future runs. Published reviews and historical verdicts are never rewritten.

### Seeding the normal acceptance fixture

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

Seven teams carry realistic data across the migration phases. Six `acceptance-*` teams
(`acceptance-core`, `-batching`, `-suppression`, `-blast-radius`, `-fire-patterns` and
`-unseen`) carry fixtures pinned to exact timestamps and exact expected outcomes; they exist
so the acceptance manifest can be computed by hand.

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
src/                     the application package; the import name is `src`
  cli.py                 command line; a run always names one team
  versions.py            frozen ruleset / prompt / parser versions
  config/                environment configuration, split by what it configures
    env.py                 reading the environment and .env
    elasticsearch.py       Elasticsearch settings
    sql.py                 SQL Server settings
    llm.py                 on-prem model settings, and the 200-alert ceiling
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
  db/                    connection, repositories, and migrations
    migrations/            Alembic environment; the DDL stays in .sql beside it
    ledger.py              the checksum ledger Alembic itself does not keep
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

Deliberately, and recorded in design section 7.4: any comparison between runs in the
scorecard or exports (the one scoped exception is the estimated time to retire v1 on the team
summary, design section 7.14), a cross-team leaderboard, historical backfill, the
company-wide unattributed-alert audit, panel discovery or live Grafana variable retrieval,
and a BI-side migration-invariant alert identity.

The first post-MVP step, the frontend, is delivered as the read-only review portal (design
section 7.10). The next is deterministic historical backfill oldest-first with no LLM calls.
