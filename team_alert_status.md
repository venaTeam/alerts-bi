# Mock Alert Dataset — Team Status Report

**Generated:** 2026-08-26
**Source:** `scripts/generate-mock-alerts.mjs` → local ES mock (`docker-compose.yml`, indices `appchi-v1` / `appchi-v2`)
**Purpose:** synthetic, realistic multi-team dataset for exercising the Alerts BI MVP described in [`alerts_bi_design.md`](alerts_bi_design.md), per the "mock environment first" decision in section 6. This is test fixture data, not real company data.

A full Elastic Cloud on Kubernetes deployment isn't available in this environment (no reachable Kubernetes cluster) and the design doc explicitly calls for a mock built from real examples before the on-prem system is reachable, so the "ECK" here is a single-node Elasticsearch 8.15 container standing in for it — same wire protocol and query surface, just not the operator-managed multi-node deployment.

Company-wide totals: **39,525 rows** (39,355 v1 / 170 v2) across **7 teams**, spanning every migration phase, plus an explicit **Unattributed** bucket (design doc §6) for alerts that match no team's registry.

---

## Regenerated 2026-08-26 — what changed and why

Two corrections, both from facts confirmed after the first generation:

**1. The v1 and v2 repeat intervals are different, and they are 144× apart.** Appchi v1's notification policy repeats a still-active Grafana alert every **5 minutes**; Appchi V2 repeats every **12 hours** (design doc §1.1). The original dataset was generated on a 12-hour cadence for both, so v1 volumes were ~144× too low. Alert *durations* are unchanged — the same alerts fire for the same lengths of time — but v1 row counts now reflect the real cadence, taking the dataset from 596 rows to 39,525.

Only Grafana-provider alerts are re-fired by the notification policy. API-provider alerts are sent by the client at whatever cadence it chooses, so those keep their authored cadence (see open question §7.3.3 — the real API re-fire behaviour is not yet known and is deliberately not invented here). This is why the heartbeat alerts, which are nearly all API-sent, stay in the tens of rows while stuck Grafana alerts run into the thousands.

**2. The v2 `key_field` hash composition was wrong.** It hashed `application|component|message|environment`. The confirmed composition is a hash of **every field except `status`, `message`, and the time fields** — so `severity`, `impact`, `runbook_url`, `operator`, `node_name`, `alert_rule_url` and the rest are all *in* the key, and `message` is *out* (design doc §1.3, §3.7). Distinct-key counts happen to be unchanged by the fix, because each alert definition in this dataset already differs in `alert_rule_url` or `node_name`.

**Why this matters more than the raw numbers:** the dataset now exercises the two things the design doc's §3.3 warns about and the old one could not. Row counts are dominated by re-fire duration to an extreme degree, and `alerts` is meaningless across schemas — see the summary table below, where a *fully migrated* team shows 19 rows and an *untouched* one shows 16,322.

---

## How to read this report

- **Rows (`alerts`)** — raw document count, dominated by re-fire volume. Comparable **within** a schema only, never across (design doc §3.3).
- **Distinct (`distinct_alerts`)** — `COUNT(DISTINCT application + key_field)`, measured against the loaded index, i.e. how many different things actually fired. This is the only number valid for cross-schema comparison. It can be lower than the number of alert *definitions* where two definitions share an `application` + `object` + `node_name` triple in v1.
- **Flagged rows** — rows matching a bad-alert rule from `what_is_an_incorrect_alert_EN.md` / design doc §4. A row can match more than one rule.
- **Core rules (1–7)** apply to both schemas; **V2 rules (8–10)** only exist in v2 (design doc §3.6 — kept separate so migrating doesn't make a team look worse).
- **Rule 6 (spam volume) is deferred to post-MVP** (design doc §4). Its counts are still generated and shown here so the data is ready when the rule is turned on, but the MVP does not treat alert quantity as evidence of a bad alert.
- **Suppressed** — rows that match the *suppression* predicates in the team's own Grafana panel query (design doc §3.2). These are **included** in every count above; suppression is reported as its own signal, never used to shrink a team's numbers.

Rule key: **1** generic/short message · **2** info/heartbeat wording · **3** placeholder metadata · **4** not metric-based · **5** self-suppressed · **6** spam volume *(post-MVP)* · **7** missing/invalid `time_created` (v1 only) · **8** missing `impact` (v2) · **9** missing `runbook_url` on critical (v2) · **10** impact restates technical cause (v2)

---

## Company-wide summary

| Team | Phase | Quality | v1 rows | v1 distinct | v2 rows | v2 distinct | Schemas |
|---|---|---|---:|---:|---:|---:|---|
| **payments-core** | Done (migrated) | Good | 0 | 0 | 19 | 10 | v2 only |
| **legacy-batch-jobs** | Phase 0 — not started | Bad | 11,852 | 10 | 0 | 0 | v1 only |
| **fraud-detection** | Phase 0 — mid-cleanup | In-between | 882 | 7 | 0 | 0 | v1 only |
| **checkout-api** | Phase 1 — dual-run | In-between | 1,026 | 9 | 17 | 10 | v1 + v2 |
| **notifications-svc** | Phase 1 — dual-run (gaming it) | Bad | 8,692 | 10 | 35 | 10 | v1 + v2 |
| **search-platform** | Phase 2 — enriching | Good | 291 | 3 | 16 | 12 | v1 (tail) + v2 |
| **data-pipeline-etl** | Phase 1 — dual-run, untouched | Bad | 16,322 | 10 | 80 | 10 | v1 + v2 |
| **Unattributed** | — | — | 290 | 2 | 3 | 2 | v1 + v2 |
| **Total** | | | **39,355** | **51** | **170** | **54** | |

This spread deliberately covers every axis the design doc cares about: a team fully done (payments-core), a team that hasn't started at all (legacy-batch-jobs), one mid-phase-0 (fraud-detection), two mid-phase-1 dual-runs with opposite quality trajectories (checkout-api vs. data-pipeline-etl), one phase-1 team that recreated its v1 suppression filter in v2 (notifications-svc — the exact "migrated the schema and nothing else" case design doc §3.2/§3.4 warns about), and one phase-2 team actively enriching (search-platform). Bad alerts appear in v1 only, v2 only, and both, depending on the team — not uniformly.

**Read the two volume columns against each other.** payments-core is *finished* and shows 19 rows; data-pipeline-etl has done *nothing* and shows 16,322. Almost none of that gap is cleanup — it is the 5-minute versus 12-hour repeat interval. Distinct counts tell the real story: 10 versus 10. This dataset exists partly to make sure the BI never reports the first pair as progress.

---

## payments-core — Done, Good

**Schema:** v2 only. **Operator:** `payments-core` (v2 operator is always 1:1 with the team by construction).
**Panel query:** `SELECT * FROM appchi_v2_hot WHERE operator = 'payments-core'` — pure identity predicate, **zero suppression**.

10 distinct alerts, 19 rows, **zero flagged rows** on any rule (core or V2). Every alert has a specific metric-based message, correct severity, a symptom-oriented `impact` (e.g. *"Customers cannot complete checkout; revenue loss accruing"*, not *"high cpu"*), and a `runbook_url`. This is the reference "what done looks like" team — fully off v1, no suppression admission left to clean up, 100% of alerts phase-2-ready.

---

## legacy-batch-jobs — Phase 0 (not started), Bad

**Schema:** v1 only — hasn't touched v2. **Operators:** `batch-team`, `BATCH_JOBS`, `batch_svc` (three inconsistent free-text spellings for one team — the exact v1 attribution problem in design doc §3.1).
**Panel query:** `SELECT * FROM appchi_v1_hot WHERE operator IN ('batch-team','BATCH_JOBS','batch_svc') AND node_name != 'legacy-heartbeat-node' AND message NOT LIKE '%test%'` — the `node_name !=` and `message NOT LIKE` clauses are **suppression predicates**, quoting back the team's own admission that some of their alerts are garbage.

10 distinct alerts, 11,852 rows. Flagged rows by rule:

| Rule | Rows | Distinct alerts |
|---|---:|---:|
| 1 — generic/short message | 9,795 | 3 |
| 6 — spam volume *(post-MVP)* | 8,354 | 2 |
| 3 — placeholder metadata | 2,306 | 2 |
| 7 — missing/invalid `time_created` | 577 | 1 |
| 2 — heartbeat wording | 32 | 2 |
| 5 — self-suppressed | 32 | 2 |
| 4 — not metric-based | 5 | 1 |

Two distinct alerts alone (`Error Occurred` and a generic sibling) account for 8,354 of the 11,852 rows — a single stuck, badly-named alert re-firing every 5 minutes for weeks. This is the archetypal phase-0 team: hasn't started cleanup, hasn't touched v2, and its own panel query is actively hiding two heartbeat definitions from whoever looks at their dashboard.

Note the shape of the suppression number: 32 rows across 2 distinct alerts, against 11,852 rows total. Both suppressed alerts are API-sent heartbeats on a 12-hour client cadence, so they are near-invisible by row count while being 20% of the team's distinct alert inventory. **This is why rule 5 must be reported on distinct alerts, not on rows** — by volume it looks like a rounding error; by inventory it is a fifth of their phase-0 work list.

---

## fraud-detection — Phase 0 (mid-cleanup), In-between

**Schema:** v1 only. **Operator:** `fraud-detection` (single consistent value — a sign this team already cleaned up its attribution even before starting on alert quality).
**Panel query:** `SELECT * FROM appchi_v1_hot WHERE operator = 'fraud-detection' AND node_name != 'fraud-canary-test'` — one residual suppression clause.

7 distinct alerts (9 definitions, two pairs sharing an `application` + `object` + `node_name` triple), 882 rows. Flagged: **145 rows / 1 distinct** on rule 1 (one generic-message alert), **10 rows / 1 distinct** on rule 2 (heartbeat) which is also the team's **1 self-suppressed distinct alert (10 rows)**. The rest are clean, metric-based and properly named. This team illustrates "actively cleaning" — volume is low *for v1*, most of what's left is legitimate, and the one canary heartbeat alert in their suppression clause is their whole remaining phase-0 punch list.

---

## checkout-api — Phase 1 (dual-run), In-between

**Schema:** v1 + v2, genuinely dual-running (design doc §3.4 — rising v2 volume here is the plan working, not a metric of progress on its own). **v1 operators:** `checkout`, `Checkout-API`. **v2 operator:** `checkout-api`.
**v1 panel query:** `SELECT * FROM appchi_v1_hot WHERE operator IN ('checkout','Checkout-API') AND node_name NOT LIKE 'test-%'` (still suppressing). **v2 panel query:** `SELECT * FROM appchi_v2_hot WHERE operator = 'checkout-api'` (clean — no suppression carried over).

**v1** — 9 distinct, 1,026 rows: rule 1 (289 rows / 1 distinct), rule 2 & 5 (heartbeat + self-suppressed, same alert, 8 rows / 1 distinct), rule 4 (2 rows / 1 distinct, not metric-based).

**v2** — 10 distinct, 17 rows: rule 4 (3 rows — an `api`-provider alert with no `alert_rule_url`), rule 8 (2 rows — a critical gateway alert missing `impact`), rule 9 (2 rows — a critical checkout-flow-breaker alert missing `runbook_url`, currently still `firing`). 7 of 10 v2 distinct alerts are fully clean and enriched.

Notably, checkout-api's v2 panel query **dropped** the `node_name NOT LIKE 'test-%'` suppression clause its v1 query still carries — this team is quietly fixing hygiene as part of migrating, not just copy-pasting the old filter forward.

---

## notifications-svc — Phase 1 (dual-run), Bad — recreated its suppression in v2

**Schema:** v1 + v2. **v1 operators:** `notifications`, `notif-svc`, `NOTIF_TEAM`. **v2 operator:** `notifications-svc`.
**v1 panel query:** `SELECT * FROM appchi_v1_hot WHERE operator IN ('notifications','notif-svc','NOTIF_TEAM') AND node_name != 'notif-canary' AND application != 'sms-gateway-test'`.
**v2 panel query:** `SELECT * FROM appchi_v2_hot WHERE operator = 'notifications-svc' AND node_name != 'notif-canary'` — **the same `node_name != 'notif-canary'` suppression clause, carried over verbatim into the new schema.**

This is the deliberate example of design doc §3.2/§3.4's warning: *"A team that recreates its filters in V2 has migrated the schema and nothing else."*

**v1** — 10 distinct, 8,692 rows: rule 1 (7,779 rows / 3 distinct), rule 6 (6,626 rows / 2 distinct — spam, post-MVP), rule 3 (1,153 rows / 1 distinct), rule 7 (577 rows / 1 distinct), rule 2 & 5 (heartbeat + suppressed, 42 rows / 3 distinct, same underlying alerts), rule 4 (4 rows / 1 distinct).

**v2** — 10 distinct, 35 rows: rule 8 (27 rows / 4 distinct — missing `impact`), rule 2 & 5 (22 rows / 2 distinct — the recreated heartbeat/suppression), rule 9 (5 rows / 2 distinct — missing `runbook_url` on critical), rule 1 (3 rows / 1 distinct), rule 10 (1 row — an alert whose `impact` field just restates the technical cause, `"high bounce rate"`, instead of the operational symptom). Only 3 of 10 v2 distinct alerts are actually clean.

**This team is the clearest demonstration of why `alerts` cannot be compared across schemas.** Their v2 rows are 35 out of 8,727 — **0.4%** of their volume — which on a naive dashboard reads as "barely started." By distinct alerts it is 10 of 20, i.e. **50%**, which is exactly where a mid-phase-1 team should be. The row-based number is not a pessimistic estimate of progress; it is measuring the notification policy.

---

## search-platform — Phase 2 (enriching), Good

**Schema:** v1 (small legacy tail, explicitly labeled "pending v2 cutover" in the message text) + v2 (primary). **Operator:** `search-platform` in both schemas.
**Panel queries:** `SELECT * FROM appchi_v1_hot WHERE operator = 'search-platform'` and `SELECT * FROM appchi_v2_hot WHERE operator = 'search-platform'` — no suppression in either.

**v1 tail** — 3 distinct legacy alerts, 291 rows, all clean, all scheduled for decommission once the v2 equivalents are trusted. Note that "3 clean alerts" still produces 291 rows at a 5-minute cadence — a good illustration that v1 row volume says nothing about quality on its own.

**v2** — 12 distinct, 16 rows. Flagged: rule 9 (2 rows / 1 distinct — one high-severity-turned-critical router alert missing `runbook_url`), rule 8 (1 row / 1 distinct — a volume-spike alert missing `impact`). 10 of 12 v2 distinct alerts are fully enriched with `impact` + `runbook_url`. This is the "almost done" team — schema migration essentially complete, phase 2 enrichment ~83% through by distinct-alert count.

---

## data-pipeline-etl — Phase 1 (dual-run), Bad — worst offender, needs chasing first

**Schema:** v1 + v2, both untouched by cleanup. **v1 operators:** `data-pipeline`, `dp-team`, `ETL_TEAM`, `pipeline` (four inconsistent spellings). **v2 operator:** `data-pipeline-etl`.
**v1 panel query:** `SELECT * FROM appchi_v1_hot WHERE operator IN ('data-pipeline','dp-team','ETL_TEAM','pipeline') AND node_name NOT IN ('dp-heartbeat','dp-test-node') AND message NOT LIKE '%OK%'`.
**v2 panel query:** `SELECT * FROM appchi_v2_hot WHERE operator = 'data-pipeline-etl' AND node_name NOT IN ('dp-heartbeat','dp-test-node')` — again, the `dp-heartbeat`/`dp-test-node` suppression is recreated in v2.

Highest volume and highest bad-rate team in the dataset.

**v1** — 10 distinct, 16,322 rows: rule 1 (13,972 rows / 4 distinct), rule 6 (12,243 rows / 3 distinct spam alerts, post-MVP), rule 3 (3,170 rows / 2 distinct), rule 5 (1,479 rows / 3 distinct — self-suppressed), rule 7 (865 rows / 1 distinct), rule 2 (38 rows / 2 distinct), rule 4 (5 rows / 1 distinct). Only 1 of 10 v1 distinct alerts is clean.

**v2** — 10 distinct, 80 rows: rule 8 (76 rows / 7 distinct — missing `impact` on the large majority of their v2 alerts), rule 9 (48 rows / 5 distinct — missing `runbook_url` on critical alerts, including two still actively `firing`), rule 2 & 5 (28 rows / 2 distinct — recreated suppression), rule 4 (3 rows / 1 distinct), rule 10 (2 rows / 1 distinct — `impact: "high cpu usage"`, restating cause not symptom). Only 2 of 10 v2 distinct alerts are clean.

This team also carries the dataset's one **large** self-suppression case: 1,479 suppressed rows, driven by a Grafana-sent placeholder alert (`object: 'Test'`, `node_name: 'dp-test-node'`) firing every 5 minutes and hidden by their own `node_name NOT IN (...)` clause. Against legacy-batch-jobs' 32 suppressed rows, it shows both ends of the range the suppression metric has to handle.

Started dual-running in v2 but did essentially zero cleanup work in either schema — this is the team a leaderboard view (design doc §7.5.3) should surface first.

---

## Unattributed

Per design doc §6 output shape: *"anything that matches no team's registry must be reported as an explicit `Unattributed` row... If it silently vanishes, company totals under-report and every team looks better than it is."*

| Schema | Rows | Distinct | Operators seen |
|---|---:|---:|---|
| v1 | 290 | 2 | `ghost-team-alpha`, `unregistered-legacy-cron` |
| v2 | 3 | 2 | `unmapped-svc-9`, `api-key-not-in-registry` |

These simulate two realistic sources of orphaned alerts: a v1 team that never had a Grafana panel built for it (so no identity predicate exists anywhere to claim its alerts), and a v2 API key that was provisioned but never registered against a team in the ownership registry.

This bucket is what the **reverse sweep** (design doc §6) is for. Neither of these operator values appears in any panel query, so ownership expansion cannot reach them from any seed — the only way to find them is to enumerate every operator value present in the index and subtract everything claimed. The operators column above is exactly the work list that sweep should produce.

---

## Bad-alert rule coverage across the dataset

Confirms every rule in design doc §4 fires at least once, and that rules 8–10 (V2-only) and rule 7 (v1-only, by construction) are both exercised:

| Rule | Teams triggering it | Total rows | Total distinct |
|---|---|---:|---:|
| 1 — generic/short message | legacy-batch-jobs, fraud-detection, checkout-api, notifications-svc (v1+v2), data-pipeline-etl | 31,983 | 13 |
| 2 — heartbeat wording | legacy-batch-jobs, fraud-detection, checkout-api, notifications-svc (v1+v2), data-pipeline-etl (v1+v2) | 180 | 13 |
| 3 — placeholder metadata | legacy-batch-jobs, notifications-svc, data-pipeline-etl | 6,629 | 5 |
| 4 — not metric-based | legacy-batch-jobs, checkout-api (v1+v2), notifications-svc, data-pipeline-etl (v1+v2) | 22 | 6 |
| 5 — self-suppressed | legacy-batch-jobs, fraud-detection, checkout-api, notifications-svc (v1+v2), data-pipeline-etl (v1+v2) | 1,621 | 14 |
| 6 — spam volume *(post-MVP)* | legacy-batch-jobs, notifications-svc, data-pipeline-etl | 27,223 | 7 |
| 7 — missing/invalid `time_created` (v1 only) | legacy-batch-jobs, notifications-svc, data-pipeline-etl | 2,019 | 3 |
| 8 — missing `impact` (v2 only) | checkout-api, notifications-svc, search-platform, data-pipeline-etl | 106 | 13 |
| 9 — missing `runbook_url` on critical (v2 only) | checkout-api, notifications-svc, search-platform, data-pipeline-etl | 57 | 9 |
| 10 — impact restates cause (v2 only) | notifications-svc, data-pipeline-etl | 3 | 2 |

Rows can double-count across rules (e.g. a heartbeat alert is typically both rule 2 and rule 5, since teams suppress the heartbeats they themselves know are noise).

**The rows and distinct columns disagree violently, and that is the point.** Rule 1 has 31,983 rows across 13 distinct alerts — roughly 2,500 rows per alert. Rule 2 has 180 rows across the same 13 distinct alerts, because heartbeats are API-sent on slow client cadences. By row count rule 1 looks 178× more important than rule 2; by inventory they are identical. A report that shows only one of these two columns tells a team the wrong thing about where to start.

---

## Regenerating this dataset

```bash
docker compose up -d
```

```bash
node scripts/generate-mock-alerts.mjs
```

```bash
STATS_ONLY=1 node scripts/generate-mock-alerts.mjs
```

The generator is seeded (`mulberry32`, fixed seed), so re-running `STATS_ONLY=1` reproduces identical stats. Re-running without `STATS_ONLY` appends a fresh, differently-`id`'d copy of every row rather than replacing — delete and recreate the indices first for a clean reload:

```bash
curl -s -X GET localhost:9200/appchi-v1/_mapping > /tmp/v1.json && curl -s -X DELETE localhost:9200/appchi-v1
```

Preserve each index's mapping before deleting it (`GET /appchi-v1/_mapping`, `GET /appchi-v2/_mapping`), then `PUT` it back on recreation — the Kibana data views key off the index names and keep working as long as the names and mappings are unchanged.
