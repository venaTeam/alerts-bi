# Mock Alert Dataset — Team Status Report

**Last updated:** 2026-08-30
**Fixture clock:** `2026-08-25T18:00:00Z`
**Reported window:** `[2026-08-18T18:00:00Z, 2026-08-25T18:00:00Z)` — the exact 168 hours a run reports

This describes the **synthetic fixture**, not any real team. It is documentation of the
mock, and it is deliberately **not an acceptance oracle**: the oracle is the hand-authored
[`test/fixtures/expected-results.json`](../test/fixtures/expected-results.json), which covers
the four `acceptance-*` teams. The seven realistic teams exist to give the pipeline
lifelike shapes and volumes to run against.

## Rewritten 2026-08-30 — what changed and why

The previous version of this file described per-team phase and quality against the
**withdrawn pairing metric** and the **old phase table**, both superseded by design section
3.4. Every figure below is now produced by the pipeline itself, under the settled
definitions:

- phase is **derived** from distinct identity presence and phase-2 readiness, never
  self-reported;
- all counts cover only the 168-hour window, so a team's older fixture data is invisible
  here by design;
- `distinct` figures are the **sum of daily distinct counts**, which is what the published
  per-day rate divides by seven;
- four `acceptance-*` teams were added; they are pinned to exact timestamps and exact
  expected outcomes.

Regenerate with a clean reload, then reproduce any row below:

```bash
RESET=1 uv run python scripts/generate_mock_alerts.py
```

```bash
uv run alerts-bi run --team <team_id> --run-at 2026-08-25T18:00:00Z --fake-llm
```

## How to read this

`alerts` is the raw row count. `distinct` is the sum of daily distinct
`application + key_field` counts. `flagged` counts **rows** with at least one core finding.
`suppressed` is a subset of `flagged`, not an addition to it. `unmeasured` counts
suppression leaves that were detected but could not be evaluated safely.

Two counts always travel together because they separate two different failure modes: a
team with 4,355 rows over 7 distinct identities has something stuck, while a team with
404 rows over 404 identities has a genuinely large inventory.

## Company-wide summary, in-window

| Team | Phase | Readiness | v1 alerts | v1 distinct | v2 alerts | v2 distinct | flagged rows | suppressed | unmeasured |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| payments-core | done | 100% | 0 | 0 | 5 | 3 | 0 | 0 | 0 |
| legacy-batch-jobs | phase_0 | — | 4,355 | 36 | 0 | 0 | 4,350 | 27 | 0 |
| fraud-detection | phase_0 | — | 733 | 10 | 0 | 0 | 154 | 9 | 0 |
| checkout-api | phase_1 | 50% | 875 | 10 | 10 | 6 | 296 | 7 | 0 |
| notifications-svc | phase_1 | 33.3% | 4,074 | 38 | 28 | 16 | 3,807 | 39 | 1 |
| search-platform | done | 100% | 0 | 0 | 5 | 3 | 0 | 0 | 0 |
| data-pipeline-etl | phase_1 | 0% | 6,655 | 44 | 61 | 35 | 6,716 | 55 | 0 |
| acceptance-core | phase_1 | 50% | 13 | 12 | 6 | 6 | 8 | 0 | 0 |
| acceptance-batching | done | 100% | 0 | 0 | 404 | 404 | 0 | 0 | 0 |
| acceptance-suppression | phase_0 | — | 6 | 6 | 0 | 0 | 1 | 1 | 2 |
| acceptance-blast-radius | phase_0 | — | 5 | 5 | 0 | 0 | 0 | 0 | 1 |

A dash under readiness means **no v2 identities fired in this window**, which is stored as
`null` and never as zero: a team cannot fail a measurement that was never taken.

---

## The seven realistic teams

### payments-core — done

Fully migrated, well-formed alerts. Only 5 rows over 3 identities reach the window because
v2 re-fires every 12 hours rather than every 5 minutes — the single most important
arithmetic fact in this dataset. No core findings, no readiness gaps, 100% readiness, and
with no v1 identities the derived phase is `done`.

### legacy-batch-jobs — phase_0, the classic stuck-alert shape

4,355 rows over 36 daily-distinct identities: **roughly 622 rows per distinct alert**. That
is one thing stuck and re-firing, not a team flooding the pipeline, and it is precisely why
`alerts` and `distinct_alerts` are always published side by side.

Rules fired: `R1 4034/17`, `R2 27/15`, `R3 577/3`, `R4 866/5`, `R5 27/15`, `R7 1/1`
(row count / distinct-identity count).

Its panel hides a heartbeat node and anything matching `%test%`, so those alerts are its
own written admission of what to delete — the phase-0 work list.

### fraud-detection — phase_0, mid-cleanup

733 rows over 10, with a much lower flagged share than legacy-batch-jobs (154 flagged rows,
`R1 145/1` dominating). Hygiene is visibly better; one noisy generic-message rule accounts
for most of the volume.

### checkout-api — phase_1, dual-run

Both schemas present, so the derived phase is `phase_1` regardless of readiness. Readiness
is 50%. Its v1 panel excludes `node_name NOT LIKE 'test-%'`, which produces 7 suppressed
rows over 4 identities.

### notifications-svc — phase_1, and a demonstration of the blast-radius guard

The interesting team. Its v2 panel carries `node_name != 'notif-canary'`, which in this
window would exclude **20 of its 28 owned v2 rows — 71%**. The guard refuses to apply it:
the leaf is counted in `suppression_unmeasured` and routed to human review rather than
marking most of the team's v2 inventory bad on a single parse artefact.

Its v1 panel is applied normally and yields 39 suppressed rows over 21 identities.

### search-platform — done *in this window*

Reports `done`, which is worth reading carefully: its v1 tail sits **outside** the reported
168 hours, so no v1 identity fired in the measured week. The phase describes only alerts
that fired in the window and cannot see silent rules or an external inventory. A run over a
wider window would show `phase_1`.

This is not a defect in the fixture; it is the documented limitation of deriving phase from
the alert stream rather than the alert inventory.

### data-pipeline-etl — phase_1, worst offender

6,655 v1 rows over 44, with **every** row carrying at least one core finding, plus 61 v2
rows all flagged and 35 identity-days of phase-2 gaps. Readiness 0%. No identity is
LLM-eligible, because every one of them already has a deterministic finding — which is the
short circuit working exactly as designed: a team with this much regex-detectable breakage
does not need a second, advisory opinion first.

---

## The four acceptance teams

These are pinned fixtures, defined in
[`scripts/acceptance_teams.py`](../scripts/acceptance_teams.py). Every row sits on
`2026-08-20T12:00:00Z` or `2026-08-21T12:00:00Z` with an exact expected outcome, so
[`test/fixtures/expected-results.json`](../test/fixtures/expected-results.json) can be
computed by hand.

### acceptance-core — one row per rule boundary

13 v1 rows over 11 identities and 6 v2 rows over 6, covering:

| Case | Expected |
|---|---|
| clean Grafana alert | no finding, goes to the model |
| API alert with no rule URL | **no R4** — API alerts carry no rule URL |
| `time_created` = `@timestamp` | valid, inclusive upper boundary |
| `time_created` = `@timestamp − 24h` | valid, inclusive lower boundary |
| `time_created` = `@timestamp + 1ms` | R7 |
| `time_created` = `@timestamp − 24h − 1ms` | R7 |
| message `Error Occurred` | R1 |
| message `i am alive` | R2 |
| `object` = `Unknown` | R3 |
| Grafana alert with no rule URL | R4 |
| identity spanning two dates, bad row on the second | R1 on 08-21 only; whole identity withheld from the model |
| v2 missing `impact` | R8, not completion-ready |
| v2 `critical` with no runbook | R9, **blocks** completion |
| v2 `high` with no runbook | R9 visible, does **not** reduce readiness |
| v2 `impact` = `high cpu` | R10, not completion-ready |
| v2 message `completed successfully` | R2 — core rules apply to both schemas |

Readiness is exactly 3 of 6 = 50%.

### acceptance-batching — the grouping and partition paths

401 v2 identities share one `alert_rule_url`, so that group must split into balanced
partitions of **134 / 134 / 133** — never 200 / 200 / 1. Three API alerts carry no rule URL
and fall back to `application` grouping, which must never merge with the rule-URL group.
Four batches in total.

### acceptance-suppression — multi-panel safety

Six rows and two panels that deliberately disagree:

| Node | Panel A | Panel B | Result |
|---|---|---|---|
| `junk-node` | excluded | excluded | **suppressed** (R5) |
| `real-node-1` | excluded (message) | visible | not suppressed — unanimity fails |
| `or-nested-node` | visible | named inside an `OR` | **unmeasured** |
| `query-var-node` | visible | named by a `query` variable | **unmeasured** |
| `real-node-2`, `real-node-3` | visible | visible | not suppressed |

Result: 1 suppressed row, 2 unmeasured leaves.

### acceptance-blast-radius — the 50% guard

One panel excluding 3 of 5 owned rows (60%). The leaf is refused, so nothing is suppressed
and one leaf is counted as unmeasured.

---

## Unattributed alerts

The dataset also contains alerts under operators belonging to no registry team
(`ghost-team-alpha`, `unregistered-legacy-cron`, `unmapped-svc-9`,
`api-key-not-in-registry`). They are a **negative control**: because a run queries only the
selected team's configured operators, these must never appear in any team's output, and an
integration test asserts exactly that.

The company-wide unattributed-alert audit that would report them is post-MVP (design
section 7.4).
