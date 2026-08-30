import { readFileSync } from 'node:fs';
import path from 'node:path';
import { PRINCIPLE_CATALOG } from '../rules/catalogs.js';
import { PROMPT_VERSION, RULESET_VERSION } from '../versions.js';
import { sha256Text } from '../util/hash.js';

/**
 * The cached prompt prefix (design section 5.1).
 *
 * Both guides go in VERBATIM, not distilled into a shorter rubric. They are the published
 * standard, and a team disputing a verdict will quote their exact wording, so the model
 * judges against the same text rather than against our paraphrase of it. Drift between a
 * distilled rubric and the guides would also be invisible: the rubric would look
 * self-consistent while no longer matching what teams were told to do.
 *
 * Any change to this procedure, either guide, or the R/P catalogue requires a new
 * PROMPT_VERSION.
 */

const GUIDE_FILES = ['Alerting_Guide_Appchi_EN.md', 'what_is_an_incorrect_alert_EN.md'];

/**
 * The fixed per-alert decision procedure.
 *
 * Two instructions here exist specifically to counter batching's known failure mode: the
 * model must assess every alert independently and may not copy a neighbour's verdict
 * merely because it is similar. Batch neighbours are context, not a template.
 */
const INSTRUCTIONS = `You are assessing production alerts against your organisation's published alerting
standard. The two guides above ARE that standard; judge only against them.

INPUT
The user message is one JSON request containing:
  - batch_id: echo this back unchanged.
  - group: the alert rule URL or application these alerts share.
  - shared_fields: source-document fields whose values are identical for EVERY alert in
    this request.
  - alerts: one entry per alert, each with alert_id, schema (v1 or v2) and fields.

For each alert, reconstruct its complete document by merging shared_fields with that
alert's fields. Judge only from that reconstructed document and the guides above. Do not
invent missing context and do not assume facts the document does not state.

The other alerts in this request come from the same alert rule or application. Use them
only as context - for example, to see whether a message is genuinely per-instance or one
generic string repeated across many nodes. Assess every alert INDEPENDENTLY. Never copy a
neighbour's verdict merely because the alerts look similar.

DECISION PROCEDURE
For each alert, return exactly one verdict:
  - "catalog_violation" only when the document clearly violates a named catalogue entry
    below. If several apply, choose the most actionable one; where two are equally
    actionable, cite the lowest catalogue ID.
  - "other" only for a clear violation of the guides that is absent from the catalogue.
    Never use "other" to express uncertainty.
  - "no_violation" when the evidence is ambiguous or no clear violation is present.

Default to "no_violation". A false positive costs far more trust than a false negative: a
team only has to catch us wrong once.

CONFIDENCE
  - "high": explicit evidence in the document directly establishes the violation.
  - "medium": a likely violation that depends on operational context you cannot see.
  - "low": a possible violation with substantial uncertainty.

JUSTIFICATION
Cite the relevant observed fields. At most 1000 characters. Contain no invented facts.

PRINCIPLE ID
  - "NONE" when assessment is "no_violation".
  - "OTHER" when assessment is "other".
  - Otherwise one catalogue ID. You may cite an R id for something the deterministic rules
    missed: R2 matches the literal string "i am alive"; it does not match "nightly
    reconciliation finished with 0 discrepancies", which is the same violation written by
    someone more articulate.

OUTPUT
Return ONLY a JSON object with exactly two fields: batch_id and verdicts. Each verdict has
exactly alert_id, assessment, principle_id, confidence and justification. Return one
verdict per alert_id sent, no more and no fewer, with no duplicates and no extra fields.`;

/**
 * The deterministic rules, restated so the model can cite them.
 * The full definitions live in the guides; these are the citation labels.
 */
const DETERMINISTIC_CATALOG = `R1  Generic message that states nothing about the failure
R2  Informational / heartbeat message ("that's a log, not an alert")
R3  Placeholder or missing required identity/ownership metadata
R4  Grafana alert missing its alert-rule link
R5  Self-suppressed: the team filters this alert out of its own panel
R6  Spam volume (not evaluated deterministically in this version)
R7  Invalid time_created: later than receipt, or more than 24 hours before it
R8  Missing or unusable impact (v2)
R9  Missing or invalid absolute HTTP(S) runbook_url (v2)
R10 impact restates the technical cause rather than the operational symptom (v2)`;

/**
 * @param {string} [repoRoot]
 * @returns {string}
 */
function readGuides(repoRoot = process.cwd()) {
  return GUIDE_FILES.map((file) => {
    const text = readFileSync(path.join(repoRoot, file), 'utf8');
    return `===== BEGIN ${file} =====\n${text}\n===== END ${file} =====`;
  }).join('\n\n');
}

/**
 * Build the complete system prefix.
 *
 * Stable across a run so the endpoint can cache it: the guides are roughly 5k tokens
 * together and cost almost nothing after the first call.
 *
 * @param {string} [repoRoot]
 * @returns {string}
 */
export function buildSystemPrompt(repoRoot = process.cwd()) {
  const principles = PRINCIPLE_CATALOG.map((p) => `${p.id.padEnd(4)}${p.text}`).join('\n');
  return [
    readGuides(repoRoot),
    '',
    '===== CITATION CATALOGUE =====',
    `ruleset_version: ${RULESET_VERSION}`,
    '',
    'Deterministic rules (R namespace):',
    DETERMINISTIC_CATALOG,
    '',
    'Judgment principles (P namespace):',
    principles,
    '',
    '===== INSTRUCTIONS =====',
    INSTRUCTIONS,
  ].join('\n');
}

/**
 * Identify the exact prompt used, for auditability.
 * @param {string} [repoRoot]
 * @returns {{promptVersion: string, systemPrompt: string, systemPromptHash: string}}
 */
export function buildPrompt(repoRoot = process.cwd()) {
  const systemPrompt = buildSystemPrompt(repoRoot);
  return {
    promptVersion: PROMPT_VERSION,
    systemPrompt,
    systemPromptHash: sha256Text(systemPrompt),
  };
}
