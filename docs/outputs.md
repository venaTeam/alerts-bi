# What a run produces

**Last updated:** 2026-09-24

A run writes exactly four files and no others: `scorecard.html`, `daily_metrics.csv`,
`rule_counts.csv`, `alert_worklist.csv`. This document says what is in each of them, column
by column and section by section.

It is a reference, not a rationale. *Why* each metric is defined the way it is lives in
[`alerts_bi_design.md`](alerts_bi_design.md); this file says what you are looking at. Where
the two disagree, the design wins and this file is the stale one.

Two things hold for everything below:

* **Everything is rendered from committed SQL rows.** Nothing is recomputed from
  Elasticsearch and nothing comes from in-memory pipeline state, so re-rendering a stored
  run reproduces the same bytes: `alerts-bi report --run-id <id>`.
* **One run is one team and one week.** There are no trends, deltas, baselines or
  comparisons against other teams, by design.

---

## 1. The two counts, and why both are always present

Almost every misreading of this tool comes from confusing these.

| | Means | A single stuck v1 alert |
|---|---|---|
| `alerts` | Raw **rows** — pipeline and dashboard load | ~288 rows a day |
| `distinct_alerts` | Distinct `application + key_field` **identities** — how many things actually fired | 1 |

A team needs the first number to care and the second to act. Reporting either alone tells a
story the other contradicts.

**Every distinct figure is published as a per-day rate**, `sum(daily distinct) / 7`, never as
a window total — a 7-day total is 7× a 1-day total for arithmetic reasons alone, and reading
it as "they have seven times more alerts" is the mistake the rate prevents.

**v1 and v2 row counts are never added together.** v1 re-fires a still-active alert every 5
minutes and v2 every 12 hours, so moving one alert between schemas divides its row count by
144 without anyone improving anything.

---

## 2. The five quality states

Every identity carries exactly one. They are mutually exclusive and they never overlap, so
they sum to the identity count.

| State | Means |
|---|---|
| `rule_flagged` | A deterministic rule matched. This identity was **never sent to the model** — one core finding anywhere in the window withholds the whole identity |
| `llm_flagged` | The model found a clear violation, at high confidence |
| `needs_review` | The model saw something, but not at high confidence. A person decides |
| `assessed_good` | The model examined it and found no violation |
| `unassessed` | Nobody examined it, with a stored reason — the model was off, unreachable, or the batch failed all three attempts |

**`assessed_good` is never inferred by subtraction.** `alerts - flagged` would count
everything nobody examined as fine, which is the single most tempting wrong number in the
tool. `unassessed` is reported beside it and should be zero on a healthy run.

V2 readiness gaps (R8–R10) are **orthogonal** to these five: an identity can be
`assessed_good` and still carry a phase-2 gap.

---

## 3. `scorecard.html`

Self-contained: no scripts, no external stylesheets, no remote images. It opens from disk
and renders identically on a machine with no network.

Sections, in render order:

| Section | Answers |
|---|---|
| **Run metadata** | Is this result reproducible? Team, `run_at`, the exact window bounds, registry version and SHA-256, ruleset / prompt / model versions, run id |
| **Migration phase** | Where is this team in the migration? The derived phase and phase-2 readiness percentage |
| **Volume** | How much is this team sending? v1 and v2 separately, rows and distinct-per-day, alerts per hour |
| **Data-quality diagnostics** | Are the identity counts trustworthy? `node_name_ratio` and `key_inflation_ratio` with their operands |
| **Quality** | How much of it is bad? The five states, plus phase-2 gaps |
| **Dashboard visibility** | What does the team hide from itself? Suppressed rows, unmeasured suppression leaves, and the supplied panels |
| **Rule and principle breakdown** | Which rules and principles fired, and how often |
| **Daily breakdown** | The per-day table — one row per schema per UTC date, the same figures as `daily_metrics.csv` |
| **Work list** | The actual list of things to fix, one row per identity |
| **LLM batch attempts** | What was asked of the model, and whether it answered |
| **Limitations** | What this scorecard deliberately does not say |

### Reading the metadata

Every field exists so a number can be traced back. The registry SHA-256 covers the
**complete** registry file, including teams this run did not select, so "which ownership
mapping produced this?" has an exact answer even after the registry is edited. `Model
version` reads `not used` when the model was off — and the Quality section then says plainly
that nothing was examined, rather than showing a reassuring zero.

### Phase and readiness

The phase is **derived** from identity presence and readiness, never self-reported.
Phase-2 readiness is completion-ready v2 identities over all v2 identities, and renders as
`—` when the team has no v2 identities: zero percent would assert a failure where there is
simply nothing to measure.

---

## 4. `daily_metrics.csv` — 26 columns

One row per `(schema, UTC date)`. A 168-hour window that does not start at midnight touches
**eight** dates, so a full run has 8 v1 rows and 8 v2 rows, with the first and last partial.

| Column | Meaning |
|---|---|
| `run_id` | The run these rows belong to |
| `team_id` | The selected team |
| `schema` | `v1` or `v2`. Never combine rows across this column |
| `snapshot_date` | UTC calendar date |
| `bucket_start`, `bucket_end` | The bucket's exact bounds, half-open |
| `covered_hours` | How much of that date the window actually covers — under 24 on the first and last rows |
| `alerts` | Raw row count |
| `distinct_alerts` | Distinct identities **on that date**. Sum the column and divide by 7 for the published rate |
| `alerts_per_hour` | `alerts / covered_hours` for the day |
| `node_name_numerator` | Distinct `(application, component, node_name)`, nonempty-node rows only |
| `node_name_denominator` | Distinct `(application, component)`, nonempty-node rows only |
| `node_name_ratio` | The two above. **`null` means no eligible rows — not zero** |
| `key_inflation_numerator` | Distinct `(application, key_field)`, all rows |
| `key_inflation_denominator` | Distinct `(application, component)`, all rows |
| `key_inflation_ratio` | The two above; `null` on a zero denominator |
| `flagged_by_rule` | **Rows** with at least one deterministic core finding |
| `flagged_by_rule_distinct` | Distinct identities with one, on that date |
| `flagged_by_llm`, `flagged_by_llm_distinct` | The same pair for high-confidence model findings |
| `needs_review`, `assessed_good`, `unassessed` | Identity counts for those three states |
| `phase2_gaps` | v2 identities carrying an R8–R10 readiness gap |
| `suppressed` | Rows hidden by the team's own panels. **A subset of `flagged_by_rule`, never an addition to it** |
| `suppression_unmeasured` | Suppression leaves detected but not safely evaluable. Allocated to the schema's **first** bucket and zero elsewhere, so summing the column gives the run total exactly once |

**Both operands are stored beside every ratio** so a reader can check the arithmetic instead
of trusting it, and so a ratio can be recomputed across days as
`sum(numerators) / sum(denominators)` — which is not the same as averaging the daily ratios.

---

## 5. `rule_counts.csv` — 8 columns

One row per `(schema, date, rule)` that matched at least once.

| Column | Meaning |
|---|---|
| `run_id`, `team_id`, `schema`, `snapshot_date` | Scope |
| `rule_id` | `R1`–`R5`, `R7`–`R10`. R6 is post-MVP and never appears |
| `ruleset_version` | The rule definitions in force. A movement in counts is always attributable to data or to a version, never ambiguously both |
| `match_count` | **Rows** the rule matched |
| `distinct_count` | Distinct identities it matched |

**Do not add `match_count` across rules to get "bad rows".** One row can match several rules
— a generic message with no rule URL matches R1 and R4 — so the sum double-counts. The row
count with at least one finding is `flagged_by_rule` in `daily_metrics.csv`.

---

## 6. `alert_worklist.csv` — 21 columns

One row per identity. This is the deliverable a team acts on.

| Column | Meaning |
|---|---|
| `run_id`, `schema` | Scope |
| `application`, `key_field` | The identity. Together they are the alert's primary key |
| `quality_state` | One of the five states in section 2 |
| `core_rule_ids` | Comma-separated deterministic findings, e.g. `R1,R4`. Empty when none |
| `readiness_rule_ids` | R8–R10 gaps. Orthogonal to `quality_state` |
| `llm_principle_id` | The principle the model cited, `NONE` if it found nothing, empty if it never saw this identity |
| `llm_confidence` | `high`, `medium` or `low` |
| `llm_justification` | The model's reasoning, at most 1000 characters. **Advisory, and kept separate from deterministic findings** |
| `unassessed_reason` | Why nobody examined it. Populated only for `unassessed` |
| `row_count` | Rows this identity produced in the window |
| `first_seen`, `last_seen` | Bounds of its activity |
| `severity` | The level's **name**, converted from the stored number by schema — see section 8 |
| `component` | `object` in v1, `component` in v2 |
| `node_name`, `environment`, `provider`, `alert_rule_url`, `message` | From the identity's representative row |

Every field after `row_count` comes from the **representative row**: the identity's most
recent row in the window. An alert enriched on Tuesday is judged as it stands on Friday.

**CSV safety.** Cells beginning `=`, `+`, `-` or `@` are prefixed with `'` so a spreadsheet
does not execute them, and the escape is visible in the cell rather than silently altering
the value. Line endings are CRLF per RFC 4180 — split on `\r\n`, not on universal newlines,
or a reader will see one enormous row.

---

## 7. The HTTP API

Shapes are declared as Pydantic models, so `/openapi.json` is generated from the code. The
interactive documentation is at `/docs`. Summarised here for anyone reading the repository
without a running instance.

`POST /runs` takes a JSON body:

```json
{ "team": "checkout-api", "run_at": "2026-08-25T18:00:00Z", "llm": "fake" }
```

`team` is required and never defaults. `run_at` defaults to now — pin it only against the
fixed-clock mock. `llm` is `live` (the default), `fake` or `off`, mirroring the CLI's
default, `--fake-llm` and `--no-llm`.

The response is the **scorecard HTML**, or with `Accept: application/json` a summary:

```json
{
  "run_id": "...", "team_id": "checkout-api", "phase": "phase_1",
  "phase2_readiness_pct": 50.0,
  "v1": {"rows": 875, "distinct": 5},
  "v2": {"rows": 10, "distinct": 6},
  "llm_assessed": true, "llm_eligible": 9,
  "out_dir": "out/d99db480d498edbd",
  "scorecard": "/runs/<run_id>",
  "files": {"scorecard.html": "/runs/<run_id>/scorecard.html", "...": "..."}
}
```

Every response carries `X-Alerts-BI-Run-Id`, and a run also carries `X-Alerts-BI-Team` and
`X-Alerts-BI-Out-Dir`.

| Route | Returns |
|---|---|
| `GET /healthz` | `{ok, version, checks}` with Elasticsearch and SQL Server reported separately; `503` if either is down |
| `GET /teams` | The registry's teams: `team_id`, `display_name`, `v1_operators`, `v2_operator`, panel count |
| `GET /runs/<run_id>` | That run's scorecard, re-rendered from SQL |
| `GET /runs/latest?team=<id>` | The team's most recent completed run |
| `GET /runs/<run_id>/<file>` | One of the four approved outputs. Nothing else is addressable |

Refusals: `400` unknown team, `409` a run is already in progress, `404` unknown run,
`422` a malformed request body.

---

## 8. Severity

Alerts store severity as a **number**. One scale serves both schemas, and each schema names
its levels differently, so the number alone does not identify the level — the schema does.

| code | v1 (Appchi) | v2 (Appchi V2) |
|---|---|---|
| 5 | `error` | `critical` |
| 4 | `major` | `high` |
| 3 | `warning` | `warning` |
| 1 | `clear` | `clear` |

Conversion happens once, during normalization, so **every output above carries the name, not
the number**. A code outside this table is kept as its own digits rather than nulled or
promoted to a neighbouring level.

---

## 9. What is stored but not exported

The SQL store holds an audit trail the four files do not carry. Reach for it when a number
needs defending.

| Column | In | Holds |
|---|---|---|
| `representative_doc` | `alert_findings`, `llm_verdicts` | The **complete source document** the assessment was made from. Elasticsearch retains three months; this outlives it |
| `representative_hash` | `alert_findings` | SHA-256 of that document, so "is this the same alert we judged?" is answerable exactly |
| `representative_at` | `alert_findings` | When the representative row was received |
| `findings_evidence` | `alert_findings` | Per-rule evidence: one entry per matched rule with a matched-row count and one sample. Summarised per rule, not per row, because a v1 alert re-firing every five minutes would otherwise store thousands of near-identical objects |
| `request_payload` | `llm_batch_attempts` | The exact bytes sent to the model, stored **before** the call, so a disputed verdict can be replayed |

Alert documents, credentials and complete LLM payloads never appear in logs — logs carry
identifiers, hashes, counts, timings and redacted errors only. The audit payloads live in
SQL and nowhere else.

---

## 10. The review portal

The read-only portal (design section 7.10) is not a run output: it renders **published**
weeks from the store, through four views its own SQL login can read. It is described here
because its numbers are the ones most easily misread against the scorecard's.

| View | One row per | Holds |
|---|---|---|
| `portal_reviews` | published week | team, week bounds, publication time, review note, phase, readiness |
| `portal_schema_totals` | published week and schema | events, distinct alerts, rule-flagged events, suppressed events, the five state counts, readiness gaps, alerts needing attention |
| `portal_alerts` | alert in a published week | the work-list columns plus `impact`, `runbook_url`, `alert_status` and `time_created` extracted from the stored document, and `attention_rank` |
| `portal_decisions` | human decision made on a published week | finding id, `pending` / `confirmed` / `dismissed`, note, time, operator |

**The portal's distinct count is a weekly total, and the scorecard's is a daily rate.**
Portal: distinct `application + key_field` identities in the whole 168-hour window, which is
the number of work-list rows for that schema. Scorecard and `daily_metrics.csv`:
`sum(daily distinct) / 7`. Both are correct; they answer different questions. The total is
comparable week to week only because every published week is exactly 168 hours.

**Events** in the portal are `sum(daily_metrics.alerts)` for the schema, the same number as
the scorecard's volume row. v1 and v2 are never added together.

**Work-list order** (`attention_rank`): `0` rule finding, `1` advisory model finding, `2`
needs a decision, `3` readiness gap only, `4` nothing to do. Within a rank, by event count.

**A human decision never changes a machine finding.** `quality_state` and the model's
verdict stay exactly as the run stored them; the decision is a separate, append-only row
keyed on the exact identity and finding id.

What the views never expose: the complete source document (`representative_doc`), model
request payloads, batch audit rows, the registry snapshot, or any week that is not
currently published.

---

## 11. What these outputs deliberately do not say

Recorded so they read as choices, not omissions. The full list with reasoning is design
section 7.4.

* **No comparison between runs** in the scorecard or the exports — no trends, deltas,
  baselines or improvement percentages. The tool reports one week; people compare. The
  review portal plots published weeks over time (section 10), still with no deltas.
* **No cross-team leaderboard.** Runs are independently timed, so their windows are not the
  same week.
* **No combined v1 + v2 volume conclusion.** The two schemas' row counts are not
  like-for-like, and neither are their distinct counts: v1's key is
  `application + object + node_name`, v2's is a hash of roughly a dozen fields.
* **`unseen`** — alerts a team owns that its own dashboard does not show — is a real on-call
  hazard and is not measured yet.
* **No spam-volume rule (R6).** Post-MVP; the fixtures already carry data for it.
