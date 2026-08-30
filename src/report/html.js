import { PHASE_LABELS } from '../rules/phase.js';
import { WINDOW_DAYS } from '../domain/metrics.js';
import { WINDOW_HOURS } from '../domain/window.js';
import { PRINCIPLE_CATALOG } from '../rules/catalogs.js';

/**
 * Self-contained HTML scorecard (design section 6, "Exact MVP output contract").
 *
 * Rendered only from committed SQL rows. It contains no cross-run trend, delta,
 * leaderboard or combined v1/v2 volume conclusion: the tool reports one week, and people
 * compare.
 *
 * Every alert-derived value is HTML-escaped. Messages, node names and justifications are
 * free text written by other teams and by a model, and none of it is trusted markup.
 */

/**
 * @param {unknown} value
 * @returns {string}
 */
export function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * @param {number|string|null|undefined} value
 * @param {number} [digits]
 * @returns {string}
 */
function num(value, digits = 2) {
  if (value === null || value === undefined) return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  return Number.isInteger(n) && digits === 0 ? String(n) : n.toFixed(digits);
}

/**
 * @param {number|string|null|undefined} value
 * @returns {string}
 */
function int(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString('en-US');
}

/**
 * @param {unknown} value
 * @returns {string}
 */
function isoDate(value) {
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  return String(value ?? '').slice(0, 10);
}

/**
 * @param {unknown} value
 * @returns {string}
 */
function isoInstant(value) {
  return value instanceof Date ? value.toISOString() : String(value ?? '');
}

/**
 * Roll daily rows up for one schema using the approved formulas.
 *
 * @param {any[]} daily
 * @returns {Record<string, any>}
 */
export function rollupSchema(daily) {
  const totals = {
    alerts: 0,
    distinctSum: 0,
    flaggedByRule: 0,
    flaggedByRuleDistinctSum: 0,
    flaggedByLlm: 0,
    flaggedByLlmDistinctSum: 0,
    needsReviewSum: 0,
    assessedGoodSum: 0,
    unassessedSum: 0,
    phase2GapsSum: 0,
    suppressed: 0,
    suppressionUnmeasured: 0,
    nodeNum: 0,
    nodeDen: 0,
    keyNum: 0,
    keyDen: 0,
  };
  for (const d of daily) {
    totals.alerts += d.alerts;
    totals.distinctSum += d.distinct_alerts;
    totals.flaggedByRule += d.flagged_by_rule;
    totals.flaggedByRuleDistinctSum += d.flagged_by_rule_distinct;
    totals.flaggedByLlm += d.flagged_by_llm;
    totals.flaggedByLlmDistinctSum += d.flagged_by_llm_distinct;
    totals.needsReviewSum += d.needs_review;
    totals.assessedGoodSum += d.assessed_good;
    totals.unassessedSum += d.unassessed;
    totals.phase2GapsSum += d.phase2_gaps;
    totals.suppressed += d.suppressed;
    totals.suppressionUnmeasured += d.suppression_unmeasured;
    totals.nodeNum += d.node_name_numerator;
    totals.nodeDen += d.node_name_denominator;
    totals.keyNum += d.key_inflation_numerator;
    totals.keyDen += d.key_inflation_denominator;
  }
  return {
    ...totals,
    // Row counts sum; every distinct measure is published as a per-day rate.
    alertsPerHour: totals.alerts / WINDOW_HOURS,
    distinctPerDay: totals.distinctSum / WINDOW_DAYS,
    flaggedByRuleDistinctPerDay: totals.flaggedByRuleDistinctSum / WINDOW_DAYS,
    flaggedByLlmDistinctPerDay: totals.flaggedByLlmDistinctSum / WINDOW_DAYS,
    needsReviewPerDay: totals.needsReviewSum / WINDOW_DAYS,
    assessedGoodPerDay: totals.assessedGoodSum / WINDOW_DAYS,
    unassessedPerDay: totals.unassessedSum / WINDOW_DAYS,
    phase2GapsPerDay: totals.phase2GapsSum / WINDOW_DAYS,
    // Diagnostics divide summed numerators by summed denominators, never the mean of
    // already-rounded daily ratios.
    nodeNameRatio: totals.nodeDen === 0 ? null : totals.nodeNum / totals.nodeDen,
    keyInflationRatio: totals.keyDen === 0 ? null : totals.keyNum / totals.keyDen,
  };
}

const STYLES = `
:root { color-scheme: light dark; --fg:#1a1a1a; --muted:#5b6472; --bg:#ffffff;
  --panel:#f6f7f9; --line:#dfe3e8; --accent:#1f5fa9; --warn:#8a5a00; --bad:#a3282d; }
@media (prefers-color-scheme: dark) { :root { --fg:#e8eaed; --muted:#9aa4b2; --bg:#14171c;
  --panel:#1c2027; --line:#2c323b; --accent:#79a9e8; --warn:#e0a94a; --bad:#e57b7b; } }
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
  font:15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size:1.6rem; margin:0 0 .25rem; }
h2 { font-size:1.15rem; margin:2.25rem 0 .6rem; padding-bottom:.3rem;
  border-bottom:1px solid var(--line); }
h3 { font-size:1rem; margin:1.25rem 0 .4rem; color:var(--muted); font-weight:600; }
p { margin:.4rem 0; }
.sub { color:var(--muted); margin:0 0 1.25rem; }
.meta { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:.5rem 1.25rem;
  background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:1rem; }
.meta div { display:flex; justify-content:space-between; gap:1rem; font-size:.86rem; }
.meta dt { color:var(--muted); }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:.75rem; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:.85rem 1rem; }
.card .label { color:var(--muted); font-size:.78rem; text-transform:uppercase;
  letter-spacing:.03em; }
.card .value { font-size:1.5rem; font-weight:600; margin-top:.15rem; }
.card .note { color:var(--muted); font-size:.78rem; margin-top:.15rem; }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.86rem; }
th, td { text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line);
  white-space:nowrap; }
th { color:var(--muted); font-weight:600; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
td.msg { white-space:normal; min-width:22rem; }
.tag { display:inline-block; padding:.05rem .4rem; border-radius:4px; font-size:.76rem;
  border:1px solid var(--line); background:var(--bg); }
.state-rule_flagged { color:var(--bad); border-color:var(--bad); }
.state-llm_flagged { color:var(--warn); border-color:var(--warn); }
.state-needs_review { color:var(--warn); }
.state-unassessed { color:var(--muted); }
.limits li { margin:.3rem 0; color:var(--muted); font-size:.9rem; }
.empty { color:var(--muted); font-style:italic; }
footer { margin-top:3rem; color:var(--muted); font-size:.8rem; }
`;

/**
 * @param {string} label
 * @param {string} value
 * @param {string} [note]
 * @returns {string}
 */
function card(label, value, note) {
  return `<div class="card"><div class="label">${escapeHtml(label)}</div>
<div class="value">${value}</div>${note ? `<div class="note">${escapeHtml(note)}</div>` : ''}</div>`;
}

/**
 * @param {any} run
 * @param {any[]} daily
 * @param {any[]} ruleCounts
 * @param {any[]} findings
 * @param {any[]} panels
 * @param {any[]} batchAttempts
 * @returns {string}
 */
export function renderScorecard(run, daily, ruleCounts, findings, panels, batchAttempts) {
  const bySchema = {
    v1: daily.filter((d) => d.alert_schema === 'v1'),
    v2: daily.filter((d) => d.alert_schema === 'v2'),
  };
  const rollup = { v1: rollupSchema(bySchema.v1), v2: rollupSchema(bySchema.v2) };

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alerts BI — ${escapeHtml(run.team_display_name)}</title>
<style>${STYLES}</style>
</head>
<body>
<main>
<h1>Alerts BI scorecard — ${escapeHtml(run.team_display_name)}</h1>
<p class="sub">One week of alerting for one team. This report describes the measured window
and nothing else: there is no comparison against a previous run or a baseline.</p>

${renderRunMetadata(run)}
${renderMigration(run, rollup)}
${renderVolume(rollup)}
${renderDiagnostics(rollup)}
${renderQuality(rollup, run)}
${renderVisibility(rollup, panels)}
${renderRuleBreakdown(ruleCounts)}
${renderDailyTable(daily)}
${renderWorklist(findings)}
${renderBatchAttempts(batchAttempts)}
${renderLimitations(run)}

<footer>Generated from committed SQL Server rows only. Run ${escapeHtml(run.run_id)}.</footer>
</main>
</body>
</html>
`;
}

/**
 * @param {any} run
 * @returns {string}
 */
function renderRunMetadata(run) {
  const entries = [
    ['Team', run.team_id],
    ['Run at (UTC)', isoInstant(run.run_at)],
    ['Window start (inclusive)', isoInstant(run.window_start)],
    ['Window end (exclusive)', isoInstant(run.window_end)],
    ['Registry version', run.registry_version],
    ['Registry SHA-256', String(run.registry_sha256).slice(0, 16) + '…'],
    ['Ruleset version', run.ruleset_version],
    ['Prompt version', run.prompt_version],
    ['Model version', run.model_version ?? 'not used'],
    ['LLM assessed', run.llm_assessed ? 'yes' : 'no'],
    ['Application version', run.app_version],
    ['Run id', String(run.run_id).slice(0, 16) + '…'],
  ];
  return `<h2>Run metadata</h2>
<div class="meta">${entries
    .map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v ?? ''))}</dd></div>`)
    .join('')}</div>`;
}

/**
 * @param {any} run
 * @param {Record<string, any>} rollup
 * @returns {string}
 */
function renderMigration(run, rollup) {
  const readiness =
    run.phase2_readiness_pct === null || run.phase2_readiness_pct === undefined
      ? '—'
      : `${num(run.phase2_readiness_pct, 1)}%`;
  return `<h2>Migration phase</h2>
<div class="cards">
  ${card('Derived phase', escapeHtml(PHASE_LABELS[run.phase_derived] ?? run.phase_derived), 'derived from identity presence, not self-reported')}
  ${card('Phase-2 readiness', readiness, 'completion-ready v2 identities / all v2 identities')}
  ${card('v1 distinct alerts/day', num(rollup.v1.distinctPerDay, 2))}
  ${card('v2 distinct alerts/day', num(rollup.v2.distinctPerDay, 2))}
</div>
<p class="sub">The phase describes only alerts that fired inside this window. It cannot see
silent rules or an external alert inventory.</p>`;
}

/**
 * @param {Record<string, any>} rollup
 * @returns {string}
 */
function renderVolume(rollup) {
  const block = (schema, r) => `<h3>${schema}</h3>
<div class="cards">
  ${card('Alerts (rows)', int(r.alerts), `${num(r.alertsPerHour, 2)} per hour over ${WINDOW_HOURS}h`)}
  ${card('Distinct alerts per day', num(r.distinctPerDay, 2), 'sum(daily distinct) / 7')}
</div>`;
  return `<h2>Volume</h2>
<p class="sub">Volume is displayed, not scored. No threshold declares an alert rate bad.
Row counts are never compared across schemas: one alert moving from v1 to v2 divides its
row count by 144 because of the repeat interval alone.</p>
${block('v1 (Appchi)', rollup.v1)}
${block('v2 (Appchi V2)', rollup.v2)}`;
}

/**
 * @param {Record<string, any>} rollup
 * @returns {string}
 */
function renderDiagnostics(rollup) {
  const row = (schema, r) =>
    `<tr><td>${schema}</td>
<td class="num">${r.nodeNameRatio === null ? '—' : num(r.nodeNameRatio, 3)}</td>
<td class="num">${int(r.nodeNum)}</td><td class="num">${int(r.nodeDen)}</td>
<td class="num">${r.keyInflationRatio === null ? '—' : num(r.keyInflationRatio, 3)}</td>
<td class="num">${int(r.keyNum)}</td><td class="num">${int(r.keyDen)}</td></tr>`;
  return `<h2>Data-quality diagnostics</h2>
<p class="sub">Context only: neither ratio contributes to flagged. A dash means the
denominator was zero, which is not the same as a ratio of zero.</p>
<div class="table-wrap"><table>
<thead><tr><th>Schema</th><th class="num">node_name_ratio</th><th class="num">num</th>
<th class="num">den</th><th class="num">key_inflation_ratio</th><th class="num">num</th>
<th class="num">den</th></tr></thead>
<tbody>${row('v1', rollup.v1)}${row('v2', rollup.v2)}</tbody></table></div>`;
}

/**
 * @param {Record<string, any>} rollup
 * @param {any} run
 * @returns {string}
 */
function renderQuality(rollup, run) {
  const block = (schema, r) => `<h3>${schema}</h3>
<div class="cards">
  ${card('Flagged by rule (rows)', int(r.flaggedByRule), `${num(r.flaggedByRuleDistinctPerDay, 2)} distinct/day`)}
  ${card('Flagged by LLM (rows)', int(r.flaggedByLlm), `${num(r.flaggedByLlmDistinctPerDay, 2)} distinct/day — advisory`)}
  ${card('Needs review /day', num(r.needsReviewPerDay, 2))}
  ${card('Assessed good /day', num(r.assessedGoodPerDay, 2))}
  ${card('Unassessed /day', num(r.unassessedPerDay, 2), 'should be zero')}
  ${card('Phase-2 gaps /day', num(r.phase2GapsPerDay, 2), 'readiness, not quality')}
</div>`;
  const unassessedTotal = rollup.v1.unassessedSum + rollup.v2.unassessedSum;
  const warning =
    unassessedTotal > 0
      ? `<p class="sub"><strong>${int(unassessedTotal)} identity-days are unassessed.</strong> ${
          run.llm_assessed
            ? 'Under exhaustive coverage this is a classifier failure, not a budget decision.'
            : 'This run did not call the model, so nothing was examined by it.'
        }</p>`
      : '';
  return `<h2>Quality</h2>
<p class="sub">Deterministic and LLM findings are kept in separate columns and are never
merged. LLM findings are advisory and do not count toward the headline flagged number.
"Good" is <em>assessed_good</em> — it is never inferred by subtracting flagged from total,
because that would count what nobody looked at as fine.</p>
${warning}
${block('v1 (Appchi)', rollup.v1)}
${block('v2 (Appchi V2)', rollup.v2)}`;
}

/**
 * @param {Record<string, any>} rollup
 * @param {any[]} panels
 * @returns {string}
 */
function renderVisibility(rollup, panels) {
  const suppressed = rollup.v1.suppressed + rollup.v2.suppressed;
  const unmeasured = rollup.v1.suppressionUnmeasured + rollup.v2.suppressionUnmeasured;
  const panelRows = panels.length
    ? panels
        .map(
          (p) => `<tr><td>${escapeHtml(p.panel_id)}</td><td>${escapeHtml(p.alert_schema)}</td>
<td class="num">${int(p.suppression_leaves)}</td><td class="num">${int(p.unmeasured_leaves)}</td>
<td>${escapeHtml(String(p.sql_text_hash).slice(0, 12))}…</td></tr>`,
        )
        .join('')
    : '<tr><td colspan="5" class="empty">No panel queries were supplied for this team.</td></tr>';

  return `<h2>Dashboard visibility</h2>
<p class="sub">Suppressed alerts are the team's own exclusion clauses quoted back to it —
rule 5, and the phase-0 work list. <em>suppressed</em> is a subset of flagged_by_rule, not
an addition to it. <em>suppression_unmeasured</em> counts leaves that were detected but
could not be evaluated safely, so an under-reported number is visible as under-reported
rather than passing for zero.</p>
<div class="cards">
  ${card('Suppressed (rows)', int(suppressed))}
  ${card('Suppression unmeasured (leaves)', int(unmeasured))}
</div>
<h3>Supplied panels</h3>
<div class="table-wrap"><table>
<thead><tr><th>Panel</th><th>Schema</th><th class="num">Suppression leaves</th>
<th class="num">Unmeasured</th><th>SQL hash</th></tr></thead>
<tbody>${panelRows}</tbody></table></div>`;
}

/**
 * @param {any[]} ruleCounts
 * @returns {string}
 */
function renderRuleBreakdown(ruleCounts) {
  /** @type {Map<string, {schema: string, ruleId: string, count: number, distinct: number}>} */
  const totals = new Map();
  for (const r of ruleCounts) {
    const key = `${r.alert_schema}|${r.rule_id}`;
    const entry = totals.get(key) ?? {
      schema: r.alert_schema,
      ruleId: r.rule_id,
      count: 0,
      distinct: 0,
    };
    entry.count += r.match_count;
    entry.distinct += r.distinct_count;
    totals.set(key, entry);
  }
  const rows = [...totals.values()].sort(
    (a, b) =>
      a.schema.localeCompare(b.schema) || Number(a.ruleId.slice(1)) - Number(b.ruleId.slice(1)),
  );

  const body = rows.length
    ? rows
        .map(
          (r) => `<tr><td>${escapeHtml(r.schema)}</td><td>${escapeHtml(r.ruleId)}</td>
<td class="num">${int(r.count)}</td><td class="num">${num(r.distinct / WINDOW_DAYS, 2)}</td></tr>`,
        )
        .join('')
    : '<tr><td colspan="4" class="empty">No deterministic findings in this window.</td></tr>';

  const principles = PRINCIPLE_CATALOG.map(
    (p) => `<tr><td>${escapeHtml(p.id)}</td><td>${escapeHtml(p.set)}</td>
<td class="msg">${escapeHtml(p.text)}</td></tr>`,
  ).join('');

  return `<h2>Rule and principle breakdown</h2>
<div class="table-wrap"><table>
<thead><tr><th>Schema</th><th>Rule</th><th class="num">Matching rows</th>
<th class="num">Distinct identities/day</th></tr></thead>
<tbody>${body}</tbody></table></div>
<h3>LLM principle catalogue</h3>
<div class="table-wrap"><table>
<thead><tr><th>ID</th><th>Set</th><th>Principle</th></tr></thead>
<tbody>${principles}</tbody></table></div>`;
}

/**
 * @param {any[]} daily
 * @returns {string}
 */
function renderDailyTable(daily) {
  const body = daily.length
    ? daily
        .map(
          (
            d,
          ) => `<tr><td>${escapeHtml(d.alert_schema)}</td><td>${escapeHtml(isoDate(d.snapshot_date))}</td>
<td class="num">${num(d.covered_hours, 1)}</td><td class="num">${int(d.alerts)}</td>
<td class="num">${int(d.distinct_alerts)}</td><td class="num">${num(d.alerts_per_hour, 2)}</td>
<td class="num">${int(d.flagged_by_rule)}</td><td class="num">${int(d.flagged_by_llm)}</td>
<td class="num">${int(d.suppressed)}</td></tr>`,
        )
        .join('')
    : '<tr><td colspan="9" class="empty">No daily rows.</td></tr>';
  return `<h2>Daily breakdown</h2>
<p class="sub">A rolling 168-hour window normally touches eight UTC dates, so the first and
last buckets are partial. Each row shows the hours it actually covers.</p>
<div class="table-wrap"><table>
<thead><tr><th>Schema</th><th>Date (UTC)</th><th class="num">Hours</th><th class="num">Alerts</th>
<th class="num">Distinct</th><th class="num">Alerts/h</th><th class="num">Rule</th>
<th class="num">LLM</th><th class="num">Suppressed</th></tr></thead>
<tbody>${body}</tbody></table></div>`;
}

/**
 * @param {any[]} findings
 * @returns {string}
 */
function renderWorklist(findings) {
  const actionable = findings.filter((f) =>
    ['rule_flagged', 'llm_flagged', 'needs_review'].includes(f.quality_state),
  );
  const body = actionable.length
    ? actionable
        .map((f) => {
          const rules = [f.core_rule_ids, f.readiness_rule_ids].filter(Boolean).join(' ');
          const cite =
            f.llm_principle_id && f.llm_principle_id !== 'NONE' ? f.llm_principle_id : '';
          return `<tr>
<td>${escapeHtml(f.alert_schema)}</td>
<td>${escapeHtml(f.application)}</td>
<td>${escapeHtml(f.key_field)}</td>
<td><span class="tag state-${escapeHtml(f.quality_state)}">${escapeHtml(f.quality_state)}</span></td>
<td>${escapeHtml(rules)}</td>
<td>${escapeHtml(cite)}${f.llm_confidence ? ` (${escapeHtml(f.llm_confidence)})` : ''}</td>
<td class="num">${int(f.row_count)}</td>
<td class="msg">${escapeHtml(f.message)}</td>
</tr>`;
        })
        .join('')
    : '<tr><td colspan="8" class="empty">Nothing flagged or awaiting review in this window.</td></tr>';

  return `<h2>Work list</h2>
<p class="sub">One row per distinct alert identity — never divided by seven. The full,
untruncated values are in <code>alert_worklist.csv</code>.</p>
<div class="table-wrap"><table>
<thead><tr><th>Schema</th><th>Application</th><th>key_field</th><th>State</th><th>Rules</th>
<th>Principle</th><th class="num">Rows</th><th>Message</th></tr></thead>
<tbody>${body}</tbody></table></div>`;
}

/**
 * @param {any[]} attempts
 * @returns {string}
 */
function renderBatchAttempts(attempts) {
  const failed = attempts.filter((a) => a.status !== 'succeeded');
  if (attempts.length === 0) return '';
  return `<h2>LLM batch attempts</h2>
<p class="sub">${int(attempts.length)} attempt(s) across ${int(new Set(attempts.map((a) => a.batch_id)).size)} batch(es);
${int(failed.length)} failed. A batch receives three total attempts and is retried as a
whole; after the third failure every alert in it becomes unassessed.</p>`;
}

/**
 * @param {any} run
 * @returns {string}
 */
function renderLimitations(run) {
  return `<h2>Limitations</h2>
<ul class="limits">
  <li>This is a single week. There is no trend, delta, baseline or improvement percentage,
      and no cross-team leaderboard.</li>
  <li>v1 and v2 row counts are never added together. v1 re-fires every 5 minutes and v2
      every 12 hours, so raw volume is not comparable across schemas.</li>
  <li>v1 and v2 <code>distinct_alerts</code> are not like-for-like: the v1 key is
      application + object + node_name, while the v2 key hashes roughly a dozen fields.</li>
  <li>Every distinct figure is a per-day rate. A seven-day total would be seven times a
      one-day total for arithmetic reasons alone.</li>
  <li>LLM findings are advisory and excluded from the headline flagged number until a
      human review measures precision at or above 95% for a named prompt and model
      version.</li>
  <li>Enriching a v2 alert mints a new <code>key_field</code>, so a team that just added
      <code>impact</code> or <code>runbook_url</code> can look briefly worse. The artefact
      clears within a week.</li>
  <li>Phase and readiness describe only alerts that fired in this window; silent rules and
      the external alert inventory are invisible to this tool.</li>
  ${
    run.llm_assessed
      ? ''
      : '<li>This run did not call the model, so every eligible identity is <code>unassessed</code>. An empty LLM column here means <em>not examined</em>, not <em>nothing found</em>.</li>'
  }
</ul>`;
}
