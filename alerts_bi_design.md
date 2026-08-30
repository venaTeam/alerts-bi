# Alerts BI — Design Document

**Status:** MVP design settled and implemented — see section 7 for what remains
**Last updated:** 2026-08-30

---

## 1. Background

### 1.1 Appchi

Appchi is the company-wide alert management system owned by our team. Any team in the company can send alerts into it, in one of two ways:

1. **Grafana alert rules** labeled with the Appchi notification policy.
2. **A direct HTTP request** to Appchi with the alert in the request body ("API").

Once an alert arrives, it lands in two places:

* **SQL Server — "hot alerts".** Holds only alerts that are *currently active*. This is the operational surface: teams build Grafana panels that query this database, and that is what the on-call engineer actually looks at.
* **Elasticsearch (ECK) — history.** Every alert event that ever arrived, with **3-month retention**.

The primary key of an alert is **`key_field` + `application`**. A still-active alert sent via Grafana is re-fired with the same key on the notification policy's repeat interval — and **that interval differs between the two systems**: Appchi v1 repeats every **5 minutes**, Appchi V2 every **12 hours** (confirmed 2026-08-27). That 144x gap dominates raw row counts and is the single most important fact for section 3.3.

### 1.2 The standardization project

Over the last few months we have been running a company-wide alert standardization project. It has two parts:

**A new architecture — Appchi V2.** Deployed separately from the original Appchi, with a different alert schema designed around what users actually need in order to send alerts properly.

**A written standard.** What an alert is, what a good alert is, what a bad alert is, and when and how alerts should be sent. It is captured in two guides in this repository:

* `Alerting_Guide_Appchi_EN.md` — proper alerting doctrine: the four golden signals, severity definitions and the Wake-Up Test, Impact vs Message, runbook structure, and the LVL3/LVL2 alert lifecycle.
* `what_is_an_incorrect_alert_EN.md` — a formal definition of a bad alert: lack of context, non-actionable, missing severity/impact, placeholder metadata, non-indicative content, not metric-based, invalid timestamp, plus explicit "don't do" rules for info and heartbeat alerts.

**Every team in the company is required to migrate.** That means more than a schema change: reduce firing so the pipeline stops causing alert fatigue, and set alert values so that an on-call engineer knows exactly what to do — which requires defining an **impact** and writing a **runbook** for every alert they own.

### 1.3 The two schemas

| Field | v1 (Appchi) | v2 (Appchi V2) |
|---|---|---|
| `application` | required | required |
| `object` / `component` | `object`, required | renamed to `component`, required |
| `message` | required | required |
| `severity` | `error` / `major` / `warning` / `clear`, default `error` | `critical` / `high` / `warning`, default `warning` |
| `status` | — | `firing` / `resolved` / `suspended`, default `firing` |
| `impact` | — | optional *(will become required)* |
| `runbook_url` | — | optional *(will become required)* |
| `environment` | — | `production` / `integration` / `development` / `test` / `load` |
| `site` | — | optional |
| `operator` | required, **open free text** | **derived from the API key** used to send the alert |
| `key_field` | default: application + object + node_name | default: hash of the alert |
| `time_created` | required, **user-supplied** | **system-stamped** |
| `node_name`, `network`, `alert_rule_url` | optional | optional |
| `provider` | `grafana` / `api` | `grafana` / `api` |
| `id`, `@timestamp` | system | system |

Notes that matter for measurement:

* `clear` (v1) is replaced by `status: resolved` (v2) — they are the equivalent "this is over" signal.
* `time_created` and `operator` moved from user-supplied to system-derived, which structurally eliminates two of the documented bad-alert characteristics (invalid timestamp, unrepresentative operator).
* `severity` does **not** map 1:1. `warning` maps to `warning` cleanly; `error` and `major` must be re-decided per alert against the Wake-Up Test. There is deliberately **no published mapping** — each team decides per alert using the guide.
* `impact` and `runbook_url` are optional today and will be made required later. The standard is therefore **not enforced at ingest**, which is precisely why a BI layer is needed.
* **The v2 `key_field` hash is built from every field *except* `status`, `message`, and the time fields** (confirmed 2026-08-27). Two consequences: the key is stable across 12-hour re-fires and across `firing` -> `resolved`, so `distinct_alerts` is meaningful; but the key *does* change when `severity`, `impact`, or `runbook_url` change — which is exactly what phase 1 and phase 2 ask teams to do. See section 3.7.
* **Index names and field mappings are exactly as defined in this table and in the mock indices** `appchi-v1` / `appchi-v2` (confirmed 2026-08-27). No renaming to reconcile.

### 1.4 Migration phases

Each team moves through three phases:

* **Phase 0 — Clean.** Remove bad / garbage / spam alerts, significantly reduce the volume of fired alerts, and map all remaining alerts.
* **Phase 1 — New rules.** Create alert rules using the V2 schema, running **alongside** the old ones, so the new can be compared against the old.
* **Phase 2 — Enrich.** Write a runbook for each alert and define its impact.

---

## 2. What we are trying to achieve

Report the state of one selected team's alerting for the week being measured:

* **How many alerts** the team has — as an **absolute rate**.
* **How many of those are bad**, also as an absolute volume, not only as a percentage.
* **Good alerts in the new schema.**
* **Bad alerts in both schemas.**
* **Total alerts in the old schema.**

**The tool reports numbers; people draw conclusions** (decided 2026-08-27). A run selects **one team**, reads one week of that team's alerts, and returns that team's figures. It does **not** compare against the previous run, against a baseline, or against anything else — no trend lines, no deltas, no improvement percentages. Comparison is done by the people reading the report, who have context the tool does not.

This is a deliberate narrowing of an earlier goal ("comparison against the past, so improvement over time is visible per team"), which is withdrawn. Every number the BI publishes is now a statement about a single week.

**Snapshots are still persisted, and that is not a contradiction.** Not comparing is a decision about the *report*; not storing would be a decision about the *data*, and section 3.5 explains why that one is irreversible. Each run appends the selected team's week to a store that outlives the 3-month retention, so any comparison anybody wants later is available to them.

**Volume is an absolute KPI, not a relative one.** A team firing 100 alerts an hour did not have 100 problems in that hour, and that is a volume problem whatever schema they are on. `alerts` against `distinct_alerts` is what separates the two failure modes — 100 rows/hour over 3 distinct keys is one thing stuck, 100 rows/hour over 100 distinct keys is a team genuinely flooding the pipeline.

**Volume is displayed, not scored** (decided 2026-08-27). The BI reports the numbers and does not set a threshold above which a team's alert rate is declared bad. No absolute rate is defined, and volume never contributes to `flagged`. A team seeing 100 alerts an hour next to 3 distinct keys can draw the conclusion themselves; the BI's job is to make the number impossible to miss, not to grade it.

The BI gives the standardization team a scorecard for the selected team and gives that measured team **a concrete list of what to fix**. A cross-team leaderboard is not part of the MVP (section 6).

---

## 3. Why this is not just a `GROUP BY`

Six problems shape the design.

### 3.1 Team attribution is hard in v1

In **v2** it is trivial: `operator` is derived from the API key and is 1:1 with a team.

In **v1**, `operator` is an open free-text field and teams use multiple custom values, so having a value does not identify a team. There is no reliable ownership column.

**Decided 2026-08-27 — ownership is a supplied list of v1 `operator` values, exactly mirroring how the v2 operator is supplied.** A team's registry entry is `{ team, v1_operators: [...], v2_operator }`, and the run selects every alert carrying any of those values. Nothing is parsed to establish ownership.

This supersedes two earlier positions, both now withdrawn: that the panel's SQL query *is* the ownership definition, and that it is a *seed* from which ownership is expanded. Both were attempts to recover an operator list from a query, and the reasoning that killed the first one applies to the second — **a panel query cannot define ownership, because it is a view.** A team whose panel reads `WHERE operator = 'batch-team' AND application = 'test'` also sends alerts under that operator with a different `application`; those alerts are theirs, are in the pipeline, and nobody on that team ever sees them. A query that both narrows *and* excludes cannot yield the full set no matter how carefully it is parsed. Asking for the list directly is strictly better.

Two facts make the operator list sufficient on its own:

* **`operator` values are team-exclusive.** Two teams picking the same free-text string is possible in principle but vanishingly unlikely in practice, so an operator value belongs to one team.
* **`application` values are not.** An application is owned by a specific team, but two teams may share an application *name* — they will differ in `operator`. The pair (`operator`, `application`) is team-unique while `application` alone is not, so `application` may never claim alerts. In the post-MVP company-wide attribution audit, application is used only as a **suggested** owner for unclaimed alerts whose operator is useless, offered for human confirmation and never auto-assigned.

What this buys beyond correctness: **the LLM leaves the ownership path entirely.** Section 5.2's central risk — a misclassified predicate silently changing which alerts belong to a team, with no error raised — can no longer occur, because no classification feeds ownership. The confidence routing, the delta check and the SQL-hash freeze all existed to contain that risk.

The registry stays **versioned**, for the same reason as before: an operator list edited later must be distinguishable from a team whose behaviour changed, and history must be re-runnable.

**The registry is a validated JSON document at `config/teams.json`** (decided 2026-08-29), with a checked-in JSON Schema. Its root contains `registry_version` and `teams`. Each team contains a stable unique `team_id`, mutable `display_name`, `v1_operators`, nullable `v2_operator`, and optional `panels`. A panel contains a stable panel id, target schema, SQL text, and supplied variable definitions. `v1_operators` may be empty after migration and `v2_operator` may be `null` before migration, but at least one source operator must exist. Exact operator values are case-sensitive and may not be assigned to more than one team; case variants used by one v1 team are listed explicitly. Registry validation completes before any ES query.

Every run stores the declared `registry_version`, SHA-256 of the complete registry document, and the selected team entry as an immutable JSON snapshot. The repository carries a mock registry built from the existing mock teams; production values replace or extend the data without changing the application contract.

This is a v1-only problem. v2's `operator` is derived from the API key and is 1:1 with a team, so ownership there is exact — one more thing the migration structurally fixes.

### 3.2 ...but their queries also hide their garbage

Before the standardization project, teams commonly sent alerts they knew were junk and simply filtered them out of their own panel:

```sql
WHERE operator = 'team-x' AND node_name != 'our-not-relevant-alert'
```

Those exclusions must **not** be applied when counting — the garbage alerts are still theirs, still in the pipeline, and still the thing this project exists to eliminate.

More importantly, an exclusion clause is a team's own written admission that an alert is worthless. It is the only place that admission exists in machine-readable form anywhere in the company. That is why the panel query is still read — but **only for this**, and never for ownership (section 3.1). Each WHERE clause is split into:

* **Identity predicates** — *which alerts are mine* (`operator IN (...)`, `application IN (...)`, `node_name LIKE 'team%'`). Ignored; ownership comes from the registry (section 3.1).
* **Suppression predicates** — *which of my alerts I hide* (`!=`, `NOT LIKE`, `NOT IN`). The only thing extracted.

Distinguishing suppression from mere panel *scoping* still matters and is not optional — see section 5.2.

**The parse is now a diagnostic, and its risk profile is inverted.** When it fed ownership, a misclassified predicate corrupted every number for that team, silently. Now a misparse can only make one visibility number slightly wrong — it cannot touch volume, quality or attribution. That makes the step **optional**: a team with no panel, an unparseable panel, or a panel nobody can find gets null visibility columns and the run completes normally.

A team that migrates properly drives suppression to zero, because the standard says to delete the garbage at the source rather than filter it downstream. A team that recreates its filters in V2 has migrated the schema and nothing else — and this is the only metric that catches that.

**Scope narrowed 2026-08-27 — the query yields exactly one output.** An alert a team deliberately filters out of its own panel is **marked as a bad alert** (rule 5) and counted in `suppressed`. Nothing else is derived from the query.

Two categories considered earlier are dropped:

* `out_of_scope` — panel narrowing on a classification dimension (`severity = 'critical'`). Legitimate, and reporting it added a number nobody would act on.
* `unseen` — alerts outside the panel's identity narrowing, which the team therefore never sees. Correctness no longer depends on this, because ownership comes from the operator list and already includes them (section 3.1). What is lost is the ability to *tell a team* that its dashboard does not show a portion of the alerts it owns. That is a real on-call hazard and it is now invisible; it can be added later from the same parse without changing anything else.

`suppressed` and rule 5's count are the same number by construction — one is the headline column, the other its per-rule row.

### 3.3 Counting grain

A raw row count is dominated by *duration*, not by how noisy a team is, and it can drop for the wrong reason (a long-running alert finally resolving). The differing repeat intervals (section 1.1) make this far worse than it first appears:

| | repeat interval | one stuck alert produces |
|---|---|---|
| v1 | 5 minutes | 288 rows/day, ~8,600 rows/month |
| v2 | 12 hours | 2 rows/day, ~60 rows/month |

**Decision:** keep counting simple, but always store **two** numbers side by side:

* `alerts` — row count (pipeline / dashboard load)
* `distinct_alerts` — `COUNT(DISTINCT application + key_field)` (how many distinct things fired)

**Exact MVP rollups** (decided 2026-08-29): within each UTC bucket, `alerts` is its raw-row count, `distinct_alerts` is its distinct (`application`, `key_field`) count, and `alerts_per_hour = alerts / covered_hours`. On the 168-hour scorecard, `alerts` is the sum of bucket row counts, `alerts_per_hour = alerts / 168`, and the published `distinct_alerts` is `sum(daily distinct_alerts) / 7`, labelled **distinct alerts per day**. An identity appearing on two UTC dates contributes once to each date because this measures average daily inventory, not unique identities across the whole week. The complete-window distinct count is still used internally for deduplication but is not the published headline. V1 and V2 remain separate; raw row counts are never added across schemas for migration conclusions.

This is one extra `COUNT(DISTINCT)` in the same query and requires no incident-collapsing machinery, but it makes "lots of noise" distinguishable from "one thing stuck".

**Scale** (measured 2026-08-27): company-wide, roughly **3,000,000 alert rows per day** across roughly **30,000 distinct alerts per day** (`application` + `key_field`). Both figures are daily. That is ~100 rows per distinct alert per day — consistent with the 5-minute v1 repeat interval and a mix of continuously-stuck and short-lived alerts.

Those two numbers together are the clearest statement of the problem this project exists to measure: **about 99% of the daily volume is the same alerts repeating, not new information.**

**Keys do not recur across days** (2026-08-27). Company-wide, the distinct count scales linearly with the window — roughly **210,000 across all teams for 7 days** and 2,700,000 across the full 3-month retention. A single-team run processes only that team's share. Two consequences:

* **`distinct_alerts` is reported as a daily rate, never as a window total.** A 7-day total is 7x a 1-day total for arithmetic reasons alone, so it is not comparable between teams measured over different spans. Every published distinct figure carries its window.
* **A run classifies every key belonging to the selected team in its window that it has not seen before.** Storing verdicts on `application` + `key_field` assumed a key seen today would be seen again tomorrow; it will not. So within a window the store's job is collapsing that team's rows onto keys. Across runs it does something else useful: because runs are ad hoc and may overlap (section 6), a re-run for a team over days already covered costs almost nothing. See section 5.1.

Worth noting the linkage: message templating was dropped from section 5.1 precisely because key-level dedup looked sufficient. Non-recurring keys are exactly the condition under which a coarser, node-independent cache key would have earned its cost back. That is not reopened here, but it is where to look first if per-alert classification cost ever needs to come down.

**And they quantify the headline-number trap.** Those same 30,000 distinct alerts, unchanged in every other respect, would produce roughly **60,000 rows a day on V2's 12-hour interval instead of 3,000,000 on v1's 5-minute one**. A 98% "reduction" is available to anyone who migrates and cleans up nothing at all. Any volume figure this BI publishes has to be immune to that, which is why phase 0's metric is `distinct_alerts` within v1 (section 3.4).

**Consequence — `alerts` is not comparable across schemas.** Moving a single alert from v1 to v2 and changing nothing else divides its row count by 144. No normalisation or compensation is applied when counting; `alerts` is simply reported per schema and read as within-schema pipeline load. **Every cross-schema and migration-progress comparison uses `distinct_alerts`, never `alerts`.**

**The headline-number trap.** Company-wide row volume will fall by roughly two orders of magnitude as teams migrate, almost entirely because of the repeat interval rather than because anyone deleted a bad alert. A "we reduced alerts by 99%" figure drawn from `alerts` would be an artefact of the notification policy and would take credit the cleanup work has not earned. Phase 0's volume-reduction metric (section 3.4) is therefore measured **within v1 only**, on `distinct_alerts`, and a team's v1 volume dropping because its alerts moved to v2 counts as phase-1 progress, not phase-0 cleanup.

This is about not taking *credit* for the drop. It does **not** retire volume as a KPI: absolute alert rate keeps mattering after migration exactly as much as before (section 2), and a post-migration team still firing far more alerts than it has problems is still a team with a volume problem.

### 3.4 Phase 1 is a deliberate dual-run

In phase 1, new rules run *alongside* old ones by design. So v1 + v2 double-counts during that phase, and rising v2 volume is neither progress nor fatigue — it is the plan working.

**Consequence:** "share of alerts on V2" is **not** a migration-progress metric. A team mid-phase-1 and a team deep in phase 1 both show around 50%. Each phase needs its own headline metric instead:

| Phase | Headline metric (this week only) | Done when |
|---|---|---|
| 0 — Clean | v1 `distinct_alerts` rate; `suppressed` count | suppression = 0, volume low, inventory mapped |
| 1 — New rules | v1 and v2 `distinct_alerts` side by side | v1 reaches zero |
| 2 — Enrich | % of v2 alerts with `impact` + `runbook_url` | 100% on `critical` |

All three are point-in-time figures for the week being reported. None of them is a comparison — per section 2, the tool does not compare runs.

**Pairing coverage is withdrawn as the phase-1 metric** (decided 2026-08-27). It assumed teams port each v1 rule to a v2 counterpart; they do not — **teams rebuild in v2 rather than port** (confirmed 2026-08-27), so there is frequently no counterpart to pair with. Phase 1 is therefore read off v1 declining while v2 exists, and phase 1 is done when v1 reaches zero.

**What that loses, stated plainly:** the BI cannot distinguish *"consolidated 40 noisy rules into 3 good ones"* from *"dropped monitoring."* Both look like v1 falling and a little v2 appearing. Detecting lost coverage requires the alert **inventory**, not the alert **stream**, and that is LVL2's Stage B review in `Alerting_Guide_Appchi_EN.md` — not something this tool can see.

A team's current phase should be **derived from distinct identity presence and phase-completion readiness** rather than self-reported, because self-reported progress drifts optimistic. The exhaustive rule is: no v1 and no v2 identities → `no_data`; v1 present and v2 absent → `phase_0`; both present → `phase_1`; v1 absent, v2 present and `phase2_readiness_pct < 100` → `phase_2`; v1 absent, v2 present and readiness = 100 → `done` (exact labels decided 2026-08-29). `no_data` avoids claiming that an empty seven-day window proves a team has not migrated. V1 reaching zero by itself does not mark the team done if phase-2 readiness is incomplete. Like every phase conclusion here, this describes only alerts that fired in the measured week and cannot see silent rules or an external inventory.

### 3.5 Retention is destroying the record right now

Elasticsearch holds 3 months. The project is already a few months old, so the pre-project period is expiring at a rate of one day per day, permanently.

**Consequence:** the persistence job matters more than the report. Reports can be regenerated from stored aggregates at any time; history cannot be recovered once it ages out. Even with manual runs, **every run must append a timestamped snapshot to storage that outlives the 3-month window.**

This survives the section 2 decision that the tool does not compare runs. The report being a single-week snapshot is a choice about what to *show*; letting the underlying weeks age out of ES unrecorded would be a choice about what to *keep*, and only one of those can be reversed later. Storing is close to free — a few hundred rows a week.

Because runs are ad hoc (section 6), the store will have gaps. A gap is recoverable by re-running over those days **while they are still inside the 3-month retention**, and permanent afterwards. That is the only deadline in this design.

**Historical backfill is deferred until after the post-MVP frontend** (ordering revised 2026-08-29). The MVP persists the daily facts covered by each normal 7-day run, but does not sweep the rest of Elasticsearch's retained history. The first post-MVP step is the interactive frontend; the second is a deterministic, day-by-day backfill of every day still available in ES, starting with the oldest. This explicitly accepts that more retained history may expire while the frontend is built and cannot then be recovered.

### 3.6 Migrating must not make a team look worse

`impact` and `runbook_url` exist only in v2, so a migrated team gains new ways to fail. If one combined "% bad" is used, a team doing exactly what was asked can see its score drop — and the conversation becomes an argument instead of a to-do list.

**Decision:** two rule sets, reported separately.

* **Core rules** — apply to both schemas. The only thing valid for reading v1 and v2 side by side.
* **V2 rules** — schema-conformance checks that have no v1 equivalent.

### 3.7 The v2 key changes when a team does the right thing

`key_field` in v2 is a hash of every field except `status`, `message`, and the time fields (section 1.3). That composition solves the problem it was designed for — re-fires and `firing` -> `resolved` keep the same key — but it puts `severity`, `impact` and `runbook_url` **inside** the identity of the alert. Those are precisely the three fields the migration asks teams to change:

* Phase 1 re-decides `error` / `major` against the Wake-Up Test, changing `severity`.
* Phase 2 adds `impact` and `runbook_url`.

So an alert that is conceptually the same alert gets a **new** `key_field` the moment its team enriches it. The practical impact is smaller than it looks, and was scoped down on 2026-08-27:

* **The enrichment bump is transient and bounded, not a permanent inflation.** Adding `impact` re-fires only the alerts firing at that moment, once, under the new key; from then on the alert fires at exactly the same rate as before. A reporting window straddling the change sees that alert twice (old key and new key); every later window sees it once. Steady-state `alerts` and `distinct_alerts` are unaffected. No BI-side compensating key is needed.
* **One residual artefact, accepted rather than fixed** (decided 2026-08-27): for the length of one reported week after enrichment, the stale pre-enrichment keys sit in the denominator of the phase-2 metric ("% of v2 alerts with `impact` + `runbook_url`"), depressing the score of a team that just did the work. It is section 3.6's failure mode in miniature, and it is tolerated because the alternative costs more than it saves — a second identity built from (`application`, `component`, `node_name`) was considered and rejected. **`application` + `key_field` is the only alert identity used anywhere in this design.** The artefact is self-healing within a week, and since nothing is compared across weeks (section 2) its whole cost is one slightly pessimistic number in one meeting.
* **Still true and not transient:** v1 and v2 `distinct_alerts` are **not like-for-like**. v1's key is `application + object + node_name`; v2's is a hash of roughly a dozen fields, so two alerts differing only in `severity` or `environment` are one distinct alert in v1 and two in v2. Direct comparison overstates v2 permanently.

This used to matter for phase-1 pairing, and no longer does: pairing is withdrawn (section 3.4), because teams rebuild in v2 rather than port. v1 and v2 `distinct_alerts` are simply reported side by side and never joined.

`node_name` feeds both keys and **may or may not hold an ephemeral pod name — that is each sending team's own choice** (confirmed 2026-08-27), so it cannot be assumed stable or volatile globally. Where a team does put a pod name there, every restart can mint a new key and inflate that team's `distinct_alerts`. Key growth alone cannot prove node churn, especially in v2 where severity, environment, impact, runbook and other fields also feed the key.

**Node-name cardinality and key inflation are surfaced separately, never corrected** (decided 2026-08-27). The BI reports two per-team, per-schema data-quality diagnostics:

* `node_name_ratio` = distinct (`application`, `object` / `component`, `node_name`) tuples divided by distinct (`application`, `object` / `component`) pairs, using only rows with a non-empty `node_name` in both numerator and denominator. This is the average number of observed node names per eligible component scope.
* `key_inflation_ratio` = distinct (`application`, `key_field`) pairs divided by distinct (`application`, `object` / `component`) pairs. This measures how much the native alert identity expands relative to the component inventory.

**Exact diagnostic storage and rollup** (decided 2026-08-29): each UTC bucket stores `node_name_numerator`, `node_name_denominator`, `key_inflation_numerator`, and `key_inflation_denominator` alongside the two ratios. `node_name_numerator` is the distinct three-field tuple count on rows whose trimmed `node_name` is nonempty, and its denominator is the distinct (`application`, `object` / `component`) count on those same eligible rows. The key numerator and denominator use all rows. A zero denominator produces `null`, never zero. The scorecard reports the average-daily diagnostic as `sum(daily numerators) / sum(daily denominators)`, so an identity present on multiple dates contributes on each date consistently with the daily-inventory metric.

Read together, they make the cause visible: both high suggests node names are driving key inflation; high key inflation with a low node ratio points to other identity fields; high node ratio with low key inflation means many nodes exist without creating equivalent key growth. Neither metric contributes to `flagged`, consistent with section 2's decision that volume and data quality are displayed rather than scored.

Correcting it inside the metric was rejected. Collapsing `node_name` out of the key would bake in an assumption that is false for every team using the field properly, and it would hide the problem from the team causing it. The guides already imply the advice to give: the canonical good alert in `what_is_an_incorrect_alert_EN.md` puts the pod name in the **message** (`cpu in pod X usage is 90%`), not in the alert's identity.

**Closed 2026-08-27:** the BI does *not* compute a migration-invariant identity of its own. `application` + `key_field` is the alert identity everywhere, native key instability included.

---

## 4. Bad-alert rules

Derived directly from `what_is_an_incorrect_alert_EN.md`. **Alert message text is written in English** (confirmed 2026-08-27), so literal keyword matching works as written and no bilingual handling is required.

| # | Rule | v1 | v2 | Set |
|---|---|---|---|---|
| 1 | Generic message matching the versioned R1 phrase catalogue | yes | yes | Core |
| 2 | Informational / heartbeat message matching the versioned R2 phrase catalogue | yes | yes | Core |
| 3 | Placeholder or missing required identity/ownership metadata | yes | yes | Core |
| 4 | Grafana alert missing its alert-rule link (`provider = grafana` and no `alert_rule_url`) | yes | yes | Core |
| 5 | Self-suppressed (matched the team's own exclusion clause) | yes | yes | Core |
| 6 | Spam volume (same `application` + `key_field` far above expected fire rate) | yes | yes | **Post-MVP** |
| 7 | Invalid `time_created`: later than receipt time or more than 24 hours before receipt | yes | — | Core (v1 only by construction) |
| 8 | Missing or unusable `impact` | — | yes | V2 |
| 9 | Missing or invalid absolute HTTP(S) `runbook_url` | — | yes | V2 |
| 10 | `impact` exactly matches the versioned technical-cause phrase catalogue | — | yes | V2 |

**Rule 6 is deferred to post-MVP** (decided 2026-08-27). The MVP does not treat a quantity of alerts as evidence that an alert is bad, consistent with section 2's decision that volume is displayed rather than scored. Volume is still reported prominently as `alerts` against `distinct_alerts`; it simply does not contribute to `flagged`. The rule stays documented here and the mock dataset still generates matching data, so it can be switched on later without re-scoring history — which is what section 6's "persist facts, not judgments" principle exists for.

Rule 5 is the one to put in front of teams first: it is their own filter quoted back to them, and it is their phase-0 work list.

**Core rules are evaluated on every raw Elasticsearch row, then aggregated to alert identity** (decided 2026-08-29). For each rule, `count` is the number of rows that actually match and `distinct_count` is the number of distinct (`application`, `key_field`) identities with at least one matching row. `flagged_by_rule` is the union of matching rows; `flagged_by_rule_distinct` is the union of identities with at least one core match. Findings are not projected onto non-matching rows merely because they share an identity.

**LLM eligibility is decided at identity level after that row-level evaluation.** If any row for an (`application`, `key_field`) identity has a core finding anywhere in the run window, the complete identity is withheld from the LLM. Otherwise, its most recent row remains the representative document sent for classification. This deliberately accepts that one defective event prevents a second, advisory judgment for the same alert: a deterministic finding already gives the team a concrete fix, while representative-only rule evaluation could silently miss row-specific R5 suppression or R7 timestamp failures.

**Daily quality allocation follows the grain of the decision** (decided 2026-08-29). A deterministic match is attributed to the UTC bucket containing that raw row: `flagged_by_rule` counts only rows that actually match, and `flagged_by_rule_distinct` counts an identity once in each bucket where at least one of its rows matches. Findings are not copied to non-matching rows or dates. An LLM verdict belongs to the identity: a high-confidence catalogue violation projects `flagged_by_llm` onto every raw row under that identity and counts the identity once in `flagged_by_llm_distinct` for every bucket in which it appears. `needs_review`, `assessed_good`, and `unassessed` likewise count the identity once in every bucket where it appears. Scorecard row counts are sums; every distinct-identity measure is `sum(daily distinct counts) / 7` and is labelled as a per-day rate. The work list remains one row per identity without division.

**Rule 1 is an exact, versioned generic-phrase match; message length alone never flags an alert** (decided 2026-08-29). Normalize `message` by trimming it, converting it to lowercase, collapsing repeated whitespace, and removing surrounding punctuation. R1 matches only when the complete normalized message equals one of: `error occurred`, `something went wrong`, `unable to get data`, `alert triggered`, or `issue detected`. It does not use substring matching. Short but potentially meaningful messages such as `Disk full` or `OOM` continue to the LLM unless another core rule matches.

**Rule 2 uses the same normalization and exact whole-message semantics as R1** (decided 2026-08-29). R2 matches only: `i am alive`, `ok`, `healthy`, `started`, `completed`, `running`, `service started`, `process running`, or `completed successfully`. It does not use substring matching: `backup completed with 10 failures` and `service is not healthy` do not match R2 and continue through the remaining checks. New phrases require a `ruleset_version` change.

**Rule 3 checks only identity and ownership metadata, with required and optional fields treated differently** (decided 2026-08-29). Normalize values by trimming, converting to lowercase, and collapsing repeated whitespace. On both schemas, R3 checks `application`, `operator`, and the schema's component field (`object` in v1, `component` in v2); it also checks `node_name` only when that optional field is supplied. A complete normalized value equal to `unknown`, `test`, `default`, or `n/a` matches. Empty or whitespace-only values match only on the required fields `application`, `operator`, and `object` / `component`; an absent or empty `node_name` is valid. Matching is exact, so `test` matches while `test-payments-service` does not. Other optional fields, including `site`, `network`, and `alert_rule_url`, are outside R3 and follow their own semantics.

**Rule 8 treats an absent or unusable V2 impact as a readiness gap** (decided 2026-08-29). R8 matches when `impact` is missing, `null`, not a string, empty or whitespace-only, or equals `unknown`, `test`, `default`, or `n/a` after trim/lowercase/whitespace normalization. A present but poor impact such as `high cpu` is not R8; it is handled by R10 or the LLM. R8 remains outside `flagged_by_rule` and does not block LLM assessment.

**Rule 9 evaluates runbook presence and URL shape on every V2 alert** (decided 2026-08-29). R9 matches when `runbook_url` is missing, `null`, not a string, empty or whitespace-only, equals `unknown`, `test`, `default`, or `n/a` after normalization, or is not a valid absolute `http://` or `https://` URL. It is reported as a readiness gap for every severity. For `critical`, a match is a mandatory phase-2 completion failure; for `high` and `warning`, it remains visible but does not by itself prevent completion under the current 100%-on-critical criterion. R9 stays outside `flagged_by_rule` and never blocks the LLM.

**Rule 10 is a deliberately narrow deterministic proxy; semantic cause-versus-impact judgment remains with LLM principle P9** (decided 2026-08-29). Normalize `impact` by trimming, converting to lowercase, collapsing repeated whitespace, and removing surrounding punctuation. R10 matches only when the complete normalized value equals `high cpu`, `high cpu usage`, `cpu usage is high`, or `cpu is high`. It does not use substring or token-similarity matching: `high cpu causes checkout latency` continues to the LLM. R10 remains a V2 readiness gap, stays outside `flagged_by_rule`, and never blocks the LLM. Catalogue additions require a `ruleset_version` change.

**Rule 7 uses `@timestamp` as the receipt time** (decided 2026-08-27). V1 requires `time_created`, so the MVP checks validity rather than presence: the valid interval is from `@timestamp - 24 hours` through `@timestamp`, inclusive. A value later than `@timestamp` or older than 24 hours at receipt is flagged.

**Rule 4 does not apply to API alerts** (decided 2026-08-27). API alerts do not carry `alert_rule_url`, so absence of that field is not evidence against them. R4 applies only when `provider = grafana`, where the missing URL means the expected Grafana alert-rule link is absent. API alerts with no other core finding continue to the LLM and use the application fallback for batching (section 5.1).

**These ten are the deterministic set, not the whole standard.** They are what regex can decide. The LLM classifier (section 5.1) works against a wider catalogue that extends these with principles from the guides that no regex can evaluate.

**Terminology:** results are labelled **flagged**, not *bad*. Keyword detection is literal and lossy — our own guide says so — and a number that survives pushback is worth more than one that does not.

**Unmapped v1 severities are not flagged** (decided 2026-08-27). `error` and `major` are the v1 schema's own legitimate values, and there is deliberately no published mapping to v2 (section 1.3). A rule against them would flag nearly every v1 alert in the company and would penalise teams for not having migrated yet — which the phase metrics already measure, properly, and without calling anyone's alert bad for it. The v1 severity distribution is reported as an observation instead.

### 4.1 The LLM principle catalogue

The ten rules above are the **deterministic** vocabulary — `R1`-`R10`. The LLM classifier (section 5.1) cites from a second, disjoint namespace, `P1`-`P11`, drawn from the same two guides but covering the judgments no regex can make. Both namespaces are versioned together under `ruleset_version`, and **both are legal citations**: the model may cite an `R` ID for something regex missed. `R2` matches the literal string `i am alive`; it does not match `nightly reconciliation finished with 0 discrepancies`, which is the same violation written by someone more articulate.

| ID | Principle | Source | Set |
|---|---|---|---|
| P1 | Non-actionable — implies no investigation, fix, escalation or attention | incorrect §2 | Core |
| P2 | Informational — reports an event or a status rather than a problem ("that's a log!") | incorrect §2, DON'T-DOs | Core |
| P3 | States the outcome, not the failure — something failed, but not what | incorrect §5 | Core |
| P4 | Component or application name does not identify a real thing | incorrect §1 | Core |
| P5 | No environment context — the reader cannot tell where it fired | incorrect §1 | Core |
| P6 | Not grounded in a golden signal (latency / traffic / errors / saturation) | alerting guide | Core |
| P7 | `critical` that fails the Wake-Up Test (urgent + immediate damage + runbook) | alerting guide | V2 |
| P8 | Severity is not derived from impact — the two are incoherent | incorrect §3 | V2 |
| P9 | `impact` restates the technical cause rather than the operational symptom | incorrect §3 | V2 |
| P10 | The required response is robotic and should have been automated, not alerted | alerting guide, Stage A | Core |
| P11 | An internal technical cause with no user-visible symptom anywhere in the alert | alerting guide, Stage A | Core |
| `other` | escape hatch — free-text justification required, never counts toward `flagged` | — | — |

`P9` deliberately duplicates rule 10. Rule 10 is the regex proxy (`impact` contains `high cpu`); `P9` is the judgment. They mean the same thing and land in different columns — `flagged_by_rule` and `flagged_by_llm`.

`P7`, `P8` and `P9` sit in the **V2 set** for the same reason rules 8-10 do (section 3.6): they need fields v1 does not have, so counting them against a migrating team in the same column as core findings would make doing the right thing look like regression.

**The catalogue is deliberately small.** Every ID has to be something a team can be handed and act on. A principle that cannot be turned into a sentence beginning *"change your alert so that..."* does not belong here, however true it is — section 2's promise is a concrete list of what to fix, not a taxonomy.

---

## 5. Where an LLM is used

**An LLM is available in the on-prem environment** (confirmed 2026-08-27), so this section is viable as designed; the regex-plus-human-queue fallback is not needed.

**The on-prem endpoint is OpenAI-SDK compatible** (confirmed 2026-08-29). The Node.js implementation uses the regular OpenAI SDK, configured with the on-prem `baseURL`, API key, and exact model/deployment identifier. It uses Chat Completions with strict JSON-schema response formatting, the cached instructions/guides as the system prefix, and the serialized batch request as the user payload. Temperature is zero. The SDK's automatic retries are disabled: the pipeline owns the three-attempt policy, records every attempt, and must not allow hidden SDK retries to exceed it. Connection timeout is configurable, and a timeout or transport error consumes one recorded attempt like any other failed call. The dependency version is locked with the project lockfile.

**Tests use a deterministic `LlmClient` fake, not the network** (decided 2026-08-29). The OpenAI adapter and fake implement the same interface. Scripted responses are selected by (`batch_id`, attempt number) and cover success, transport error, timeout, invalid JSON, wrong batch id, missing/duplicate/extra alert ids, invalid enum relationships, failure-then-success, and three consecutive failures. Tests assert byte-identical retry payloads, exactly three pipeline attempts, no hidden retry, whole-response rejection, batch-wide `unassessed` on exhaustion, and durable verdict reuse. A live on-prem test is separate and opt-in through environment configuration.

**There is now exactly one LLM use** (decided 2026-08-27). This section began with two; 5.2's panel-SQL classification turned out to be a lookup rather than a judgment and no longer calls a model. 5.2 stays here because that is where the reasoning lives and because the shape of what was rejected is worth keeping.

The dividing line is: **deterministic rules produce numbers; the LLM produces judgments.** They are never merged into one column.

### 5.1 Fallback classifier for alerts the rules could not decide

Core deterministic rules run first and **win wherever they fire**, so the rule-flagged set stays fully explainable ("rule 2 matched the literal string `i am alive`"). Only alerts with no core finding go to the model. V2 readiness rules R8-R10 are evaluated in parallel and do **not** prevent LLM assessment (decided 2026-08-27): a missing `impact` is a phase-2 gap, but the alert must still be assessed for core problems such as being informational or non-actionable.

Important caveat: the core rules only detect *bad* — they never confirm *good*. So "core rules were inconclusive" is not a small residual; it is every alert with no core match, i.e. the large majority — and **all of it is classified**, including alerts carrying V2 readiness gaps (decided 2026-08-27). Two things make that affordable:

* **Deduplicate on `application` + `key_field` before classifying** (decided 2026-08-27). One classification per distinct alert, not per row. In the mock this is 39,355 v1 rows collapsing to 51 classifications — a 772x reduction for zero build cost, because the pipeline already computes exactly this `COUNT(DISTINCT)` for `distinct_alerts`. The verdict is stored against `(application, key_field, prompt_version, model_version)`, so the same alert always returns the same stored verdict. This also restores determinism, so two runs over the same week return the same verdicts.

  **It is a within-run collapse, not a cross-run cache** (clarified 2026-08-27). Section 3.3 measured that keys do not recur across days, so the store's real work is collapsing rows onto keys *inside* a single run; a cross-run hit is a bonus, never a saving to plan around. Calling it a cache overstates what the data supports. The durable verdict identity is (`application`, `key_field`, `prompt_version`, `model_version`); an Elasticsearch row ID is not stored as the audit reference because the source document expires after three months (decided 2026-08-29). Instead, each verdict stores `classified_at`, the complete representative document sent to the model, and a hash of that document. The identity finds the verdict after ES retention, while the stored input preserves the exact message variant and evidence the model judged. **A stored verdict is never recomputed except on a version bump.** A key that is stored but absent from the current window simply does not appear in this run's output — there is nothing to resolve, because the verdict is a property of the alert rather than of the window.

  An earlier version of this design deduplicated on a normalised *message template* instead. That is dropped: **alert messages interpolate the current metric value** (`error rate 3.4%`, then `3.9%`, then `4.1%`), so message-based dedup would not collapse re-fires anyway, and it would require building and versioning a text scrubber that the alert key makes unnecessary.

  Two consequences follow from keying on the alert rather than the text:

  * **A single verdict covers every message variant under one key.** So the document sent to the model must be a *defined* representative — the **most recent row for that key within the run window** — not an arbitrary one, or two runs over the same data could pick different rows and reach different verdicts.
  * **In v2, enriching an alert mints a new key** (section 3.7), so adding `impact` or `runbook_url` triggers a fresh classification. That is correct: the alert genuinely changed in the way the standard cares about, and the old verdict should not carry over.
* **Coverage is exhaustive — there is no classification budget** (decided 2026-08-27, **superseding** the bounded-budget decision recorded earlier the same day). Every alert with no core deterministic finding goes to the model, whether or not it has a V2 readiness gap. `unassessed` accordingly stops being a design parameter and becomes a ramp-up or failure state (below).

  **What it costs.** Company-wide, ~30,000 distinct alerts per day with no key reuse means **~210,000 classifications** if every team is run over the same 7-day period. Each actual run classifies only the selected team's share. Marginally that is roughly 400 input tokens per alert (the alert document — the two guides ride in the cached prefix) and ~100 output tokens. A run overlapping a previous run for the same team costs less, because the overlapping keys already have stored verdicts.

  **There is no cold-start ramp.** A team's first live run classifies that team's distinct keys from exactly the same 7-day window used by every later run. LLM coverage never runs backwards: the post-MVP history backfill is deterministic-only (section 6).

  **Alerts are batched per request, grouped by alert rule** (decided 2026-08-27). The request count for a team cannot be estimated until the production group-size distribution is measured: the one-group-per-request rule deliberately leaves small groups undersized.

  Batches are **not** arbitrary slices. Alerts are grouped by `alert_rule_url` where present; alerts without one are grouped by `application` (decided 2026-08-27). Each group is sent independently — **groups are never packed together in one request**, even when they are small — so a request never mixes alert rules or applications merely to fill capacity.

  A request carries at most **200 alerts** (decided 2026-08-27). A group of 200 or fewer becomes one request. A larger group is split into deterministic, balanced partitions: for `n` alerts, create `ceil(n / 200)` batches whose sizes differ by at most one, after sorting alerts by `key_field`. For example, 401 alerts become batches of 134, 134 and 133, not 200, 200 and 1. This avoids a tiny tail request whose single alert loses the same-group context that batching is intended to provide.

  This inverts a risk into an advantage. A judgment can be coloured by the alerts sharing its request; when the neighbours are the same rule's other instances, that context is exactly what a reviewer would want — the model can see whether a message is genuinely per-instance or the same generic string repeated across forty nodes.

  **The payload factors out only what the batch demonstrably shares.** Grouping by `alert_rule_url` or `application` does not prove that fields such as `component`, `severity`, `provider`, `impact` or `runbook_url` are identical. A field moves into the group header only when its value is equal across every alert in that batch; otherwise it remains on each alert. This is **lossless**: every full document is reconstructable from header plus row, so it is not the extraction rejected above, it is the same content sent once instead of repeatedly. The expected token reduction remains a measurement rather than a guarantee.

  **The logical request contract is fixed** (decided 2026-08-29). Each request contains `batch_id`; a `group` object with `type` (`alert_rule_url` or `application`) and `value`; `ruleset_version`; `prompt_version`; `shared_fields`; and `alerts`. Every alert contains `alert_id`, `schema` (`v1` or `v2`), and `fields`. `schema` always remains on the alert envelope. `shared_fields` contains only source-document fields whose values are identical across the complete batch; `fields` contains every remaining source-document field. Merging them must reconstruct the complete representative document exactly, and no source field may be dropped.

  `alert_id` is a transport identifier, not a second business identity: it is the SHA-256 of an unambiguous encoding of (`schema`, `application`, `key_field`). The verdict cache remains keyed exactly as already decided. `batch_id` is the SHA-256 of an unambiguous encoding of (`run_id`, group type, group value, partition index, ordered alert ids, `prompt_version`, `model_version`). The original serialized payload and both identifiers are persisted before the first attempt and reused byte-for-byte on retries. The two guides, catalogue, and classification instructions stay in the cached prompt prefix rather than being repeated in this JSON.

  Three constraints, none optional:

  * **Structured output echoing the alert id on every verdict**, with a hard check that the set of ids returned equals the set sent. A failing response is never partially accepted. The original batch is retried as a whole with identical membership, ordering and payload (decided 2026-08-29); there is no alert-by-alert fallback. Each batch receives at most **three total attempts** — the initial request plus two retries. If the third attempt fails, every alert in that batch becomes `unassessed` with the shared failure reason. A verdict attached to the wrong alert is the worst output this system can produce — it is a false positive that also destroys the audit trail that would have caught it.
  * **One verdict per alert, never one per group.** Grouping is an input-side optimisation only. Collapsing a rule to a single classification was considered and rejected: two alerts under the same rule can differ in `message`, `node_name` or `environment` in ways that matter, and the team's work list is built from alerts.
  * **Batch composition is deterministic** — groups ordered by group key, alerts within a group ordered by `key_field`, then divided using the balanced-partition rule above. Identical input must produce identical batches, or the determinism this section claims elsewhere is quietly untrue.

  **The response contract is exact and closed** (decided 2026-08-29). The top-level object contains only `batch_id` and `verdicts`. Every verdict contains only `alert_id`, `assessment`, `principle_id`, `confidence`, and `justification`. `assessment` is `no_violation`, `catalog_violation`, or `other`; `confidence` is `high`, `medium`, or `low`; and `justification` is required, nonempty after trimming, and at most 1,000 characters. `principle_id` must be `NONE` for `no_violation`, one `R1`-`R10` or `P1`-`P11` ID for `catalog_violation`, and `OTHER` for `other`. Each alert receives one primary principle — the most actionable violation when several apply.

  The response is rejected as a whole if its `batch_id` differs, its alert-id set is not exactly the request's set, an id is duplicated, an enum or assessment/principle pairing is invalid, a justification is invalid, or any additional field is present. Verdict order is immaterial because ids provide the binding. A high-confidence catalogue violation becomes `flagged_by_llm`; a medium- or low-confidence catalogue violation and every `other` result go to review; `no_violation` becomes `assessed_good`.

  **What this costs: errors now correlate within a rule.** A misjudgment lands on all of a rule's instances at once rather than on 25 scattered alerts, so `flagged_by_llm` becomes lumpier while `flagged_by_llm_distinct` stays readable. That is the right trade — a team fixes the *rule*, not the instance, so rule-sized findings match how the work list is actually used — but it means the distinct column is the one to read when the row count looks alarming.

  **Exhaustive coverage raises the stakes on guardrail 2.** Every alert examined is an alert we can be wrong about, so the false-positive surface is now the entire inventory rather than a ranked slice of it. That is an argument for keeping `flagged_by_llm` out of the headline number until precision is measured (guardrail 5) — not an argument against looking at everything.

**This replaces the "confident-good signals" idea, which was tested and rejected** (2026-08-27). The proposal was to skip alerts carrying positive structural signals — `provider = grafana`, `alert_rule_url` present, non-placeholder metadata, message above a length floor, plus `impact` and `runbook_url` for v2. Evaluated against the mock, it sent **0 of 51** v1 alerts and **3 of 54** v2 alerts to the model, waving through messages like `Unhandled exception in request pipeline` and `ETL job for warehouse sync failed with exit code 1` as good without anyone looking.

The failure is structural rather than a matter of tuning: **those signals measure provenance, not quality.** They establish that an alert was created the right way, not that it says anything useful — so anything wired through a Grafana rule passes regardless of whether its message describes a user-visible symptom or an internal stack trace. Worse, a skipped alert is never examined by anything, so its errors are invisible. Exhaustive coverage removes the question rather than tuning it: nothing is waved through on provenance, because nothing is waved through at all.

Five mutually exclusive run-level identity states (expanded 2026-08-29):

| State | Meaning |
|---|---|
| `rule_flagged` | at least one raw row under the identity has a core finding |
| `llm_flagged` | no core finding; the model returned a high-confidence catalogue violation |
| `needs_review` | no core finding; the model returned a medium/low catalogue violation or `other` |
| `assessed_good` | no core finding; the model returned `no_violation` |
| `unassessed` | no core finding; classification exhausted all three attempts, or the day predates LLM coverage |

Section 6's `good = alerts - flagged` is therefore **wrong and withdrawn**: it silently converts "we did not look" into "it is fine". A team must be able to see how much of its inventory was actually examined.

These states are assigned once per distinct (`application`, `key_field`) identity for the run. `needs_review`, `assessed_good`, and `unassessed` are stored and reported as distinct-identity counts because assessment occurs once per identity; review is neither good nor unassessed. Row and distinct counts remain paired for `flagged_by_rule` and `flagged_by_llm`. V2 `phase2_gaps` is orthogonal and may coexist with any V2 state.

* **`unassessed` is enumerated, not estimated** (decided 2026-08-27). Under exhaustive coverage there is no unexamined population left to sample: every alert in that state can be named individually, together with its reason — a day that predates LLM coverage, or a classification that failed and exhausted its retries. It is published per team and is expected to **trend to zero**. A run where it does not is a run with a broken classifier, and that is precisely the signal a sampled error bar would have smoothed away.

  The earlier proposal — a random sample of the unreached set and a published *"an estimated X% would have been flagged"* error bar — is withdrawn along with the budget it existed to measure.

**What the prompt contains:** both guides verbatim (decided 2026-08-27) — `Alerting_Guide_Appchi_EN.md` and `what_is_an_incorrect_alert_EN.md`, roughly 5k tokens together, as a **cached prompt prefix** so they cost almost nothing after the first call of a run. They are not distilled into a shorter rubric: the guides are the published standard, and a team disputing a verdict will quote their exact wording, so the model should be judging against the same text rather than against our paraphrase of it. Any drift between a distilled rubric and the guides would also be invisible — the rubric would look self-consistent while no longer matching what teams were told to do.

The catalogue of principle IDs (guardrail 3 below) rides alongside the guides as the citation vocabulary, and `prompt_version` covers the pair — so a guide revision and a catalogue addition are equally attributable.

**The prompt defines a fixed per-alert decision procedure** (decided 2026-08-29). For each alert, the model reconstructs the complete document from `shared_fields` plus that alert's `fields`, judges only from that document and the supplied guides, and does not invent missing context. Other alerts in the request may provide same-rule context, but the model must assess every alert independently and may not copy a neighbour's verdict merely because it is similar. It returns `catalog_violation` only for a clear named R/P violation; if several apply, it chooses the most actionable and uses the lowest catalogue ID as the tie-break between equally actionable choices. `other` is only for a clear violation absent from the catalogue, never for uncertainty. Ambiguous evidence defaults to `no_violation`.

The prompt defines `high` confidence as explicit document evidence that directly establishes the violation, `medium` as a likely violation that depends on operational context, and `low` as a possible violation with substantial uncertainty. Justification must cite relevant observed fields, stay within the response contract's 1,000-character limit, and contain no invented facts. Any change to this procedure, either guide, or the included R/P catalogue requires a new `prompt_version`.

**What is sent per alert:** the full alert document as it stands, not an extract (decided 2026-08-27). Once dedup happens on the key rather than on the message, there is no longer any reason to withhold context from the model — the earlier worry that richer input would weaken dedup no longer applies, and `severity`, `component`, `impact`, `provider` and `alert_rule_url` all bear directly on whether the alert meets the standard.

Guardrails:

1. **Separate columns:** `flagged_by_rule` vs `flagged_by_llm`.
2. **Bias toward good.** A false positive costs far more trust than a false negative — a team only has to catch us wrong once. Default to not-bad unless a named principle from the guides is clearly violated.
3. **Cite and score.** Output the violated principle plus a confidence. **Confidence is a three-value enum — `high` / `medium` / `low` — not a number** (decided 2026-08-27). Models are not calibrated well enough on a 0-1 scale to justify a numeric cutoff, and a float invites a threshold carrying more precision than the judgment underneath it has. `high` counts; `medium` and `low` route to the review list. The review list's eventual confirm rate is the calibration data that would justify moving that line.

   **The model is not confined to rules 1-10** (decided 2026-08-27). Those ten are the *deterministic* set — what regex can decide — and they are a deliberately lossy reading of the guides. Several principles the guides state plainly have no rule: whether a `critical` severity actually passes the Wake-Up Test, whether the alert rests on one of the four golden signals, whether `severity` and `impact` are coherent with each other, whether a component name is identifiable rather than merely non-placeholder, whether the required response is robotic enough that it should have been automated instead of alerted. Confining citations to the ten would make the model blind to exactly the judgments it was brought in for.

   **But citations are drawn from a named catalogue, not free text.** Free-form principle names do not aggregate: "lacks actionability", "not actionable" and "no clear action implied" are one finding written three ways, and the per-rule breakdown in section 6 cannot group them, so a team receives a pile of one-off strings instead of a work list. Section 2 says the BI must hand each team a concrete list of what to fix; that requires a stable vocabulary. The catalogue therefore *extends* rules 1-10 with further principle IDs drawn from the two guides, and carries `ruleset_version` so additions are attributable. **It is drafted in section 4.1** — `P1`-`P11`, a namespace disjoint from the deterministic `R1`-`R10`.

   **One escape hatch:** the model may return `other` with a required free-text justification. `other` never counts toward `flagged` — it goes to the review list. A justification that recurs is the signal to promote it into the catalogue under a new ID and a new `ruleset_version`, which is how the vocabulary grows without ever silently changing what a past number meant.
4. **Version everything.** Store `model_version` and `prompt_version` next to `ruleset_version` so any shift is attributable.
5. **Advisory, but published** (decided 2026-08-27). `flagged_by_llm` does **not** count toward the headline `flagged` number, and it is shown on every team's work list from day one anyway. Those two halves are both load-bearing: the findings are the reason the model is here at all, and a column that is never published is a column nobody builds correctly. Promotion into the headline requires a human review of a sample of LLM verdicts showing **precision at or above 95%**, measured at a named `prompt_version` and `model_version`, reviewed by the standardization team and recorded here as a decision. Any version bump resets the measurement. 95% is not arbitrary — it is guardrail 2 expressed as a number, since a team only has to catch us wrong once.

**Verdicts are pinned, never re-scored** (decided 2026-08-27). A stored verdict belongs to the `prompt_version` and `model_version` that produced it and is never recomputed under a new pair. Re-scoring the company-wide inventory is unaffordable, and it is unnecessary for everything *except* the LLM: section 6's "persist facts, not judgments" means the deterministic rules can be re-run over all stored history at any time. LLM verdicts are the one exception to that guarantee, and the honest way to carry an exception is to make its seam visible — **a version bump is stamped on every row it produced**, so weeks judged under different versions are never mistaken for like-for-like when somebody lines them up by hand. Old verdicts stay valid for the runs that produced them; new runs classify forward.

### 5.2 Parsing the team's panel SQL — suppression detection only

**Scope reduced 2026-08-27, twice.** This step no longer establishes ownership (section 3.1), and it no longer produces `out_of_scope` or `unseen` (section 3.2). Its entire job is now one thing: **find the predicates by which a team deliberately filters its own alerts out of its own panel, and mark those alerts bad under rule 5.**

Stated operationally, the parse looks for exactly one shape — **a negation on an instance-level field**:

| Class | Example | Treatment |
|---|---|---|
| **Suppression** | `node_name != 'legacy-heartbeat-node'`, `message NOT LIKE '%test%'` | **rule 5 — flagged**, and counted in `suppressed` |
| Scoping | `severity = 'critical'`, `environment != 'test'` | ignored |
| Identity | `operator IN ('batch-team','BATCH_JOBS')` | ignored — ownership comes from the registry |
| Mechanical | `$__timeFilter`, `LIMIT`, `ORDER BY` | ignored |

The discriminator is **not** the operator — scoping predicates are commonly negations too. It is **what the field identifies**:

* An **instance-level** field points at specific alerts the team knows are junk → **suppression**.
* A **classification dimension** narrows the view → **scoping**.

The field table below is the authoritative membership list for those two categories.

**This discriminator cannot be dropped along with the reporting of scoping.** Reading `severity = 'critical'` as suppression would mark a team's entire non-critical inventory as bad alerts, purely because they run a critical-only panel.

**And this inverts the risk one more time.** When the parse fed ownership, a mistake corrupted every number silently. When it fed a diagnostic, a mistake cost one advisory number. Now it feeds `flagged`, so a scoping predicate misread as suppression **marks good alerts as bad** — the single most expensive error this design can make, by section 5.1's own reasoning: a false positive costs far more trust than a false negative, because a team only has to catch us wrong once.

The mitigation is that the discriminator is a **lookup, not a judgment**: which field, and is it negated. Both are recoverable from the parse tree, so **no LLM is involved** (decided 2026-08-27). The classification is a hardcoded field table:

| Field | Class |
|---|---|
| `node_name`, `message`, `object` / `component`, `key_field`, `alert_rule_url`, `application` | instance-level — a negation here is **suppression** |
| `severity`, `environment`, `status`, `provider`, `operator` | classification dimension — ignored |
| anything else | **ignored, and the field name logged** |

Two notes on the table:

* **Unknown fields are ignored, not guessed at.** That biases the parse toward false negatives, which is the direction section 5.1's guardrail 2 demands: an unrecognised field quietly under-reports `suppressed`, whereas guessing could mark good alerts bad. The logged names are how the table grows.
* **`application` is the debatable entry.** `application != 'legacy-app'` hides an entire application's alerts from the team that owns them — the phase-0 problem in its purest form — so it counts as suppression. A team that legitimately runs one panel per application is protected by the multi-panel unanimity rule below rather than by the field table.

**Query shape** (confirmed 2026-08-27): the queries are rarely structurally complex — joins, CTEs and subqueries are not the norm — but the WHERE clause itself is often long, with many conditions, **can contain `OR` and nested parentheses**, and **may contain Grafana template variables**. A team may have **several** panel queries.

**Panel queries and their variable definitions are collected, not discovered or fetched during a run** (decided 2026-08-27; variable source revised 2026-08-29). The standardization team supplies each team's SQL and a frozen variable-definition snapshot alongside the registry entry. The MVP does not call Grafana. This removes live configuration drift from a reproducible run. Discovery was considered, because a team that omits its panel avoids rule 5 entirely — but the collection here is done **by** the standardization team rather than volunteered by the measured team, which removes most of that incentive, and discovery carries a matching risk in the opposite direction: a panel wrongly attached to a team applies that team's rule-5 findings to alerts it does not own. The panel list and frozen definitions used for each team are published with its numbers, so they can be disputed.

**Template-variable resolution** (decided 2026-08-27; input contract revised 2026-08-29). `custom`, `constant` and `interval` variables resolve from the frozen definitions supplied in the registry. Multi-value definitions include the complete selected-value list and whether “all” was selected. **`query` variables are not executed**, and missing required definitions are not guessed. Either condition inside a suppression predicate makes that leaf **present but unmeasured**: counted in `suppression_unmeasured` alongside the `OR`-nested leaves below, never silently dropped.

**Blast-radius guard** (decided 2026-08-27). If a suppression leaf would mark more than **50%** of a team's owned rows as bad, it is not applied — it is counted as unmeasured and routed to human review. This is the `$__all` failure mode: a multi-value variable expanding to everything turns `node_name != '$nodes'` into an exclusion of the team's entire inventory. Marking half a team's alerts bad on a parse artefact is the single most expensive error available here, and no legitimate suppression clause has that reach.

**Multi-panel semantics:** a row counts as `suppressed` only if **every** panel the team owns excludes it. A row filtered out of one panel but visible in another is not hidden from the team, and flagging it would be a false positive of exactly the kind this section warns about. Rows are counted once regardless of how many panels exclude them.

**Freeze the parse on a hash of the SQL text.** Same query, same stored mapping, nothing re-derived. Keeps rule 5 stable between runs on identical input.

The **delta check** — running the count with and without the suppression predicates — is dropped. It existed to detect ownership distortion, and there is no longer any ownership to distort.

**Rewrite safety.** Computing `suppressed` means evaluating the suppression predicates against the team's owned set. Real panel queries contain `OR` and nested parentheses, so a suppression leaf cannot always be lifted out cleanly: `operator = 'x' AND (node_name != 'junk' OR severity = 'critical')` changes meaning if the leaf is removed. Rule: **evaluate only suppression leaves that are AND-ed at the top level; anything nested inside an `OR` is skipped and counted as unmeasured.** Report the count of skipped leaves alongside `suppressed` so an under-reported number is visible as under-reported rather than passing for zero.

---

## 6. MVP scope

* **Manual, on-demand, single-team run, performed by our team** (decided 2026-08-27). The operator selects one team for each run. There is no schedule. The run reports that team's **last 7 days**, and that is the only window it ever reports. The window is an exact rolling 168 hours in UTC (decided 2026-08-29): capture `run_at` once, set `window_end = run_at` and `window_start = run_at - 168 hours`, and query the half-open range `@timestamp >= window_start AND @timestamp < window_end`. Gaps between runs and overlaps with a previous run are both acceptable and neither needs handling: nothing is compared across runs (section 2), so an alert appearing in two runs' output is not double counting anything, and a day nobody ran over is simply a day nobody asked about.
* **Input for a run:** one selected registry entry containing the team's list of v1 `operator` values and its v2 `operator`. The team's panel queries are separate, optional inputs used only to detect which owned alerts the team deliberately filters out of its dashboards (rule 5).
* **Mock environment first** — built from real examples. Production is on-prem, so the mock is where the pipeline is developed and tested before it is pointed at the real cluster.
* **Environment and store** (decided 2026-08-27). The tool **can reach ECK directly**, so runs query Elasticsearch live — no export step. Snapshots are appended to a **SQL Server database of our own**: small relational aggregates, the versioned registry joins cleanly, and Grafana already queries SQL Server if panels are ever wanted on top of it.
  **Local SQL Server setup** (decided 2026-08-29): extend the existing Docker Compose mock with a pinned SQL Server 2022 image, readiness health check, and persistent development volume. It hosts a persistent `alerts_bi_dev` database and a disposable `alerts_bi_test` database. Credentials come from an uncommitted `.env` with placeholders in `.env.example`. The application provides commands to apply and inspect migrations and to recreate only the explicitly named test database. Production uses the same migrations with an externally supplied connection string; tests do not substitute SQLite or another engine.
* **Elasticsearch is the sole source** (decided 2026-08-27). The SQL Server hot-alerts table holds only currently-active alerts — a subset of what ES already carries, with no historical depth, and nothing in it that ES lacks. Its one unique property is live state, which nothing in this design measures. It is read for exactly one thing and it is not read by this tool: the teams' own Grafana panels query it, which is why section 5.2 parses those queries.
* **Primary audience: the standardization team; output: one team scorecard** (decided 2026-08-27). Each MVP run generates the selected team's scorecard and work list. Teams do not self-serve in the MVP: the numbers are presented **by** the standardization team, including in company-wide meetings. A cross-team leaderboard is deferred because independently timed team runs do not share an identical reporting window.
* **Output format: a generated per-team scorecard (HTML + CSV) rendered from the store** (decided 2026-08-27). Grafana can be pointed at the same table later. The store is the non-negotiable part (section 3.5); coupling the first deliverable to a dashboard build is not.
  **Exact MVP output contract** (approved 2026-08-29): generate one self-contained HTML scorecard with sections for run metadata; derived migration phase and completion readiness; schema-separated volume and diagnostics; deterministic quality, LLM quality, review, good, unassessed and phase-2 gaps; suppression visibility and supplied panel ids; rule/principle breakdown; actionable work list; and limitations. It contains no cross-run trend, delta, leaderboard, or combined v1/v2 volume conclusion. Generate exactly `daily_metrics.csv`, `rule_counts.csv`, and `alert_worklist.csv`. Ordering is deterministic; CSV carries full values even when HTML shortens display text; all alert content is HTML-escaped; spreadsheet-formula prefixes (`=`, `+`, `-`, `@`) are neutralized in CSV; and every output is rendered only from committed SQL data.
* **Grain and window** (decided 2026-08-27; storage shape resolved 2026-08-29). A run reports the exact rolling UTC window above and nothing else — no comparison against the previous run or against a baseline (section 2). SQL Server stores one run record plus one metric row per run × team × schema × UTC calendar date touched by the window. Because a rolling 168-hour range normally touches eight UTC dates, its first and last buckets may be partial. Each daily row stores its actual `bucket_start`, `bucket_end`, and `covered_hours`; rows are assigned by the UTC date of `@timestamp`. The scorecard aggregates those daily rows over the exact run window using section 3.3's formulas. Storing at a finer grain than the report keeps any future window a presentation choice rather than a data-collection one. Per section 3.3, every distinct figure is published as a **daily rate**, never as a window sum.

Run steps:

1. Select one team and load its versioned registry entry: v1 operator list, v2 operator and optional panel queries.
2. Query ES v1 only for that team's v1 operators; query ES v2 only for its v2 operator.
3. Count.
4. Apply the direct core rules and V2 readiness rules, then parse any supplied panel queries for suppression predicates and apply core rule 5 (section 5.2). A team with no panel simply has no rule-5 findings.
5. Collapse by identity, withhold every identity carrying any core finding, and run durable-verdict lookup plus the LLM fallback for the remainder (section 5.1). V2 readiness gaps do not withhold an identity.
6. **Append the run record, daily metric rows and supporting detail** to persistent storage, then render the team's scorecard from the store.

Step 6 is the one that cannot be skipped — manual runs are fine, as long as every run appends to a store that outlives the 3-month retention.

**Post-MVP sequence.** First build the interactive frontend on top of the persisted run data and pipeline controls; its detailed scope is designed after the MVP. Then read every day still retained in Elasticsearch into the store, oldest first, using deterministic metrics only. LLM classification is not backfilled — roughly 2,700,000 historical keys would be unaffordable and unnecessary. Backfilled rows carry `llm_assessed = false`, so an empty `flagged_by_llm` means *not examined*, not *nothing found*.

### Storage and output shape

The SQL Server store has one run-level record, daily metric rows, and supporting detail tables (decided 2026-08-29).

**Run record — one row per run:**

| group | columns |
|---|---|
| identity | `run_at`, `run_id`, `team`, `window_start`, `window_end` |
| **versions** | `registry_version`, `ruleset_version`, `prompt_version`, `model_version`, `llm_assessed` |
| **phase** | `phase_derived`, `phase2_readiness_pct` |

**Daily metrics — one row per run × team × schema × `snapshot_date`:**

| group | columns |
|---|---|
| identity | `run_id`, `team`, `schema`, `snapshot_date`, `bucket_start`, `bucket_end`, `covered_hours` |
| **volume** | `alerts`, `distinct_alerts`, `alerts_per_hour`, `node_name_numerator`, `node_name_denominator`, `node_name_ratio`, `key_inflation_numerator`, `key_inflation_denominator`, `key_inflation_ratio` |
| **quality** | `flagged_by_rule`, `flagged_by_rule_distinct`, `flagged_by_llm`, `flagged_by_llm_distinct`, `needs_review`, `assessed_good`, `unassessed`, `phase2_gaps` |
| **visibility** | `suppressed`, `suppression_unmeasured` |

**Supporting detail:**

* Daily per-rule counts: (`run_id`, `team`, `schema`, `snapshot_date`, `rule_id`, `ruleset_version`, `count`, `distinct_count`).
* Alert findings and work-list rows keyed by run, schema, `application` and `key_field`, including deterministic findings, LLM state, review status and `unassessed` reason.
* LLM batch attempts, including group identity, attempt number, membership and shared failure reason when the batch exhausts all three attempts.
* Durable LLM verdicts keyed by (`application`, `key_field`, `prompt_version`, `model_version`), including `classified_at`, verdict, principle, confidence, representative document and document hash.
* Panel parses keyed by SQL-text hash, including the frozen interpretation and any unmeasured reason.

The HTML scorecard and CSV files are rendered from these stored rows. The run record carries the phase label next to the seven-day result; the daily metric rows preserve the finer reporting grain.

**The phase group is small because two of the three phase metrics are already other columns.** Section 3.4's phase-0 and phase-1 headlines are `distinct_alerts` and `suppressed`, both of which the daily volume and visibility rows already carry — so only `phase_derived` and `phase2_readiness_pct` belong on the run record. `phase_derived` is computed by section 3.4's rule and is a label, not a score: nothing in the report grades a team against it.

**`phase2_readiness_pct` is a phase-completion measure computed on distinct `application` + `key_field` identities** (decided 2026-08-27; exact formula decided 2026-08-29). Evaluate the most recent representative row for every V2 identity. An identity is completion-ready when it has no R8 gap, does not match R10, and — only when `severity = critical` — has no R9 gap. The percentage is `completion-ready V2 identities / all distinct V2 identities × 100`; with no V2 identities it is `null`, not zero. A missing runbook on `high` or `warning` remains visible in `phase2_gaps` and the work list but does not reduce this completion percentage. No alternative identity is constructed, so section 3.7's enrichment bump does depress a just-complied team's score for one week. That is accepted; see section 3.7 for why.

**`phase2_gaps` carries V2 rules 8-10, and they stay out of `flagged`** (decided 2026-08-27). They are a readiness number, not a quality number. Folding them in before `impact` and `runbook_url` are mandatory would make a migrating team appear to regress. When the fields do become required, the fold-in is a `ruleset_version` bump — so no past number silently changes meaning.

**`suppressed` is a subset of `flagged_by_rule`, not an addition to it.** It is rule 5's count promoted to a headline column (section 3.2); adding the two together double-counts. `suppression_unmeasured` counts suppression leaves that were detected but could not be evaluated — nested inside an `OR`, carrying an unresolved `query` variable, or stopped by the blast-radius guard (section 5.2) — so an under-reported `suppressed` is visible as under-reported instead of passing for zero.

Both a row count and a distinct count are carried for **flagged** as well as for volume, and for the same reason (section 3.3): one bad alert re-firing every 5 minutes is `flagged = 8,600` but `flagged_distinct = 1`. The first number is the damage it does to the pipeline; the second is the size of the fix. A team needs both — the first to care, the second to act.

The daily per-rule breakdown lets `flagged` be drilled into. **`good` is not `alerts - flagged`** — that would count unexamined alerts as good (section 5.1). Good is `assessed_good`, and `unassessed` is reported next to it. Under exhaustive coverage (section 5.1) `unassessed` should be zero on any day inside the LLM coverage window; a non-zero value is a classifier failure, not a budget decision, and reads as one.

The version fields on the run record exist so that a movement in a team's numbers can always be attributed. `registry_version` separates *their behaviour changed* from *we edited the ownership mapping*; `ruleset_version`, `prompt_version` and `model_version` do the same for *we changed what counts as bad*. `llm_assessed` is false on post-MVP backfilled history, so an empty `flagged_by_llm` there reads as absence of examination rather than absence of findings.

### Guiding principle

**Persist facts, not judgments.** `has_impact`, `provider`, `message_template`, `severity` are observations — once ES ages out they are gone forever. "This alert is bad" is a derivation, recomputable at any time. Storing the attributes means all of history can be re-scored when the rule set changes, which is exactly what will be needed when `impact` and `runbook_url` become required.

**The LLM is the one exception, and it is a deliberate one.** A model verdict is a judgment that cannot be recomputed for free, so it is stored as a pinned result rather than re-derived (section 5.1). Everything a rule needs is still persisted as a fact, which is why the post-MVP backfill can reconstruct the retained deterministic history but not the LLM's view of it.

---

## 7. Open questions

The MVP design is settled and the MVP is built. What remains is batching validation, the measurements that need a live endpoint, the post-MVP frontend and history backfill, the rule-6 deferrals, a record of what was deliberately left out, and the boundaries settled during implementation.

### 7.1 Rule-grouped batching validation (5.1)

**Mechanics resolved 2026-08-27 and 2026-08-29.** Group by `alert_rule_url`, falling back to `application` when the URL is absent. Send one group per request and never pack small groups together. Cap requests at 200 alerts; split larger groups into deterministic balanced partitions whose sizes differ by at most one. Invalid responses retry the identical batch as a unit and never fall back to individual alerts. A batch receives three total attempts; after the third failure, every member becomes `unassessed`. What remains is validation of the quality and capacity assumptions behind that policy.

1. **Does grouping actually improve judgment, or does the model just anchor?** The claim in section 5.1 is that showing a rule's instances together is context a reviewer would want — the model can see whether a message is genuinely per-instance or one generic string repeated across forty nodes. The opposite outcome is equally plausible: the model reads the first alert, forms a verdict, and echoes it down the group without looking. **This is the bet the entire grouping rests on and it is testable on the mock** — classify the same alerts grouped and ungrouped, and compare verdicts. If they diverge, find out which one is right before shipping it.
2. **Is the factored payload really lossless in the model's eyes?** Section 5.1 factors out only fields verified to be identical across the batch, so every document is reconstructable. Whether the model *uses* the factored representation identically is a separate question. Send the same alerts factored and unfactored and compare verdicts.
3. **Does a batch of up to 200 degrade per-alert judgment?** The 200-alert ceiling is a throughput choice, but it is also a quality boundary. The same alerts at smaller batch sizes and at 200 should produce the same verdicts; if they do not, lower the ceiling based on quality rather than filling the model context window.
4. **Correlated error is accepted but unquantified.** Section 5.1 records that a misjudgment now lands on a whole rule rather than on scattered alerts, and argues that rule-sized findings match how teams actually fix things. Nothing yet measures how large those lumps get. The precision review that guardrail 5 requires before promoting `flagged_by_llm` into the headline should sample **by rule**, not by alert, or it will mistake one rule-wide error for forty independent ones.

### 7.2 To measure before the first live run

1. **Confirm the on-prem model sustains the classification load.** A single-team run processes only that team's distinct alerts, while running every team over the same week would total roughly 210,000 classifications (section 5.1). Measure sustained requests/second; verify that a 200-alert request fits the endpoint's input and output limits and still returns a clean verdict per id; and measure the actual token reduction from the factored payload. **The 200-alert value is a hard count ceiling, not a claim that the model can safely consume 200 alerts under every payload shape.** If capacity or judgment quality fails below that ceiling, lower the operational limit and record the change here.
2. **Check how rule grouping distributes.** Because groups are never packed together, the distribution directly determines the request count: small groups create undersized requests, while very large groups exercise the balanced split path. Aggregate by `alert_rule_url`, with missing URLs aggregated by `application`, before the first live run.

### 7.3 Deferred with rule 6

Rule 6 (spam volume) is post-MVP (section 4). These move with it.

1. **Threshold: what fire rate counts as spam?** It must differ **by schema as well as by provider** — the expected Grafana baseline is ~288 rows/day in v1 and ~2/day in v2 (section 3.3), so a single fixed threshold would flag every healthy v1 alert and no unhealthy v2 one. Likely expressed as a multiple of the expected repeat cadence rather than as an absolute count.
2. **API-sent alerts: do they have any re-fire cadence, or are they one-shot?** Only affects the threshold above. Noted because the mock generator currently leaves API alerts on their authored cadence rather than inventing one.

### 7.4 Deliberately out of the MVP

Recorded here so they read as choices rather than oversights.

* **A BI-side migration-invariant alert identity** (section 3.7) — `application` + `key_field` only, accepting that the v2 key changes on enrichment and that v1 and v2 keys are not like-for-like.
* **`unseen` — alerts a team owns but its own dashboard does not show** (section 3.2). A real on-call hazard, currently invisible, addable later from the same parse without changing anything else.
* **`query` template variables inside suppression predicates** (section 5.2) — counted as unmeasured rather than resolved by executing a team's SQL.
* **Panel discovery or live variable retrieval via the Grafana API** (section 5.2) — panels and frozen variable definitions are collected by the standardization team instead.
* **Cross-team leaderboard** (sections 2, 6) — deferred; the MVP produces one independently timed team scorecard per run.
* **Interactive frontend** (section 6) — the first post-MVP step (ordered 2026-08-29). The MVP remains a generated self-contained HTML scorecard plus the three approved CSV exports; detailed frontend scope is designed after the MVP.
* **Company-wide attribution audit and `Unattributed` work list** (sections 3.1, 6) — deferred because it requires enumerating operators across all alerts, which conflicts with the MVP's team-filtered queries. The future audit enumerates every operator/application value, subtracts all registered operators and reports the remainder with volumes; application may suggest an owner but never assigns one automatically.
* **Historical deterministic backfill** (sections 3.5, 6) — the second post-MVP step, after the frontend, run oldest-first over everything still retained in Elasticsearch.
* **LLM classification of backfilled history** (sections 5.1, 6) — remains excluded even when the deterministic backfill is added; LLM coverage is exhaustive within each reported week and never runs backwards.
* **Any comparison between runs** (section 2) — no trends, deltas, baselines or improvement percentages. The tool reports one week; people compare.
* **Sampling and error bars on `unassessed`** (section 5.1) — withdrawn with the classification budget. Under exhaustive coverage `unassessed` is enumerable, so there is nothing left to estimate.
* **One verdict per alert rule** (section 5.1) — grouping is an input-side optimisation only. Collapsing a rule to a single classification was considered and rejected: alerts under one rule can differ in `message`, `node_name` or `environment` in ways that matter, and the work list is built from alerts.

### 7.5 Repository housekeeping

The mock and its documentation were built against earlier versions of this design and had drifted.

* [`scripts/generate-mock-alerts.mjs`](scripts/generate-mock-alerts.mjs) generates rule-6 spam data and multi-month spans that a 7-day, no-comparison run never reads. Not wrong — ahead of what the MVP consumes — but it means the mock exercises paths the pipeline does not have.
* [`team_alert_status.md`](team_alert_status.md) described per-team phase and quality against the withdrawn pairing metric and the old phase table (section 3.4).
* Neither carried `alert_rule_url` groupings dense enough to test 7.1 properly. Testing the grouping bet needs mock rules with **many** distinct alerts each, which the original generator did not reliably produce.

Reconcile before building the pipeline against the mock, or the first thing the pipeline proves will be that the fixtures are stale.

**Reconciled 2026-08-30.** The generator was extended rather than replaced, and four `acceptance-*` teams were appended after the seven realistic ones — appended last on purpose, so the seeded RNG draws consumed by the existing teams are unchanged and their generated data stays byte-stable. They are defined in [`scripts/acceptance-teams.mjs`](scripts/acceptance-teams.mjs) and cover the paths the original fixtures could not reach: both inclusive R7 boundaries and one millisecond outside each, an API alert with no rule URL that must not match R4, a multi-date identity whose finding must stay on the date that matched, all three v2 readiness rules including a non-critical R9 that must not reduce readiness, a 401-alert rule-URL group that must split 134/134/133, a missing-URL application group that must never merge with it, multi-panel suppression disagreement, an `OR`-nested leaf, an unresolved `query` variable, and a 60% blast radius. The generator also gained explicit index mappings and a guarded `RESET=1` clean reload; `team_alert_status.md` was rewritten against the settled phase and rule definitions; and [`scripts/es-scale-probe.mjs`](scripts/es-scale-probe.mjs) now reports the two approved diagnostics with their operands, scoped to one selected team.

**Rule 6 data remains in the mock and is simply not consumed**, which is the intended state: the rule stays documented and its fixtures stay generated, so switching R6 on later does not require re-seeding.

### 7.6 Boundaries settled during implementation

The MVP build hit eight cases this document did not fix. None changes an approved decision; each resolves an unstated edge in the direction the surrounding decision already points. Recorded here so they read as choices rather than as accidents of code.

* **An absent or unparseable v1 `time_created` matches R7** (2026-08-30). Section 4 says the MVP "checks validity rather than presence", which resolves how to treat a *present* value. A missing value cannot fall inside the inclusive interval, and characteristic 7 of `what_is_an_incorrect_alert_EN.md` calls a missing event timestamp a bad alert outright, so absence is flagged with its own evidence reason rather than passing silently.
* **Suppression uses positive-match semantics, not SQL three-valued logic** (2026-08-30). Strict SQL would also filter a row whose `node_name` is `NULL` out of a panel carrying `node_name != 'X'`, because the comparison is `UNKNOWN`. That is an artefact of NULL handling, not a team's written admission that an alert is worthless, and honouring it would mark every node-less alert of every team that writes a single node exclusion. A leaf therefore excludes a row only when the row's value actually matches the excluded value. This biases toward false negatives, which is the direction section 5.1's guardrail 2 demands.
* **Suppression value matching is case-sensitive** (2026-08-30). SQL Server's default collation is case-insensitive, so this is a deliberate divergence from what the panel would do. Both sides of the comparison are written by the same team and match exactly in practice, and case-insensitivity could only ever *widen* the suppression set — the one direction that marks good alerts bad.
* **`suppression_unmeasured` is allocated to the schema's first daily bucket** (2026-08-30). It counts leaves, and a leaf has no date. Repeating the run-level value on all eight rows would make the daily column sum to eight times the truth; placing it once means summing the column yields the run total exactly.
* **`run_id` is deterministic**, derived from (`team_id`, `run_at`, `window_start`, registry SHA-256, `ruleset_version`, `prompt_version`, `model_version`, application version) (2026-08-30). Re-running the same team over the same frozen `run_at` therefore replaces its own rows rather than accumulating near-identical runs, which is what makes a restarted run safe. Any genuine difference — a different clock, an edited registry, a version bump — produces a different id, so overlapping runs still coexist as section 6 requires.
* **V2 readiness gaps are read off the representative row, not unioned over the window** (2026-08-30). Readiness describes an identity's *current* state: an alert enriched on Tuesday is ready on Friday, and a gap it no longer has must not still be reported. Deterministic core findings keep the opposite treatment — any core finding anywhere in the window counts — because those describe events that actually happened.
* **Stored finding evidence is summarized per rule, not per row** (2026-08-30). Each identity stores one evidence entry per matched rule with a matched-row count and one sample. A v1 alert re-firing every five minutes would otherwise store thousands of near-identical evidence objects for no added information.
* **Derived rates are stored as `DECIMAL(18,6)`** (2026-08-30). Acceptance comparison therefore allows half a unit in the last stored place, so the manifest can record exact arithmetic (`1/24`) while the store holds `0.041667`. Counts and operands are integers and are compared exactly.

**Acceptance-data contract** (decided 2026-08-29): give the existing seeded generator a fixed default clock for acceptance runs and require a clean index reload. Reconcile its R1-R10 cases with the exact rules above; add dense rule-URL groups, missing-URL application groups, groups larger than 200, suppression safety cases, and retry cases. Check in a hand-reviewed `test/fixtures/expected-results.json` containing each mock team's daily raw/distinct counts, hourly rates, diagnostic operands and ratios, per-rule row/distinct counts, LLM batch membership, quality states, suppression results, readiness, and phase. The pipeline under test must not generate its own oracle. A verification command compares persisted SQL rows and CSV exports to this manifest; HTML acceptance tests check required structure and content rather than incidental formatting.
