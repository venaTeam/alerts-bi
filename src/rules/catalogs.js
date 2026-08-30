/**
 * Versioned phrase catalogues for the deterministic rules (design section 4).
 *
 * Every entry is already in normalized form: lowercase, single-spaced, no surrounding
 * punctuation. Adding a phrase changes what a past number meant, so any addition requires
 * a RULESET_VERSION bump.
 */

/** R1 — generic messages that state nothing about the failure. */
export const R1_GENERIC_MESSAGES = Object.freeze([
  'error occurred',
  'something went wrong',
  'unable to get data',
  'alert triggered',
  'issue detected',
]);

/** R2 — informational / heartbeat messages. "That's a log, not an alert." */
export const R2_HEARTBEAT_MESSAGES = Object.freeze([
  'i am alive',
  'ok',
  'healthy',
  'started',
  'completed',
  'running',
  'service started',
  'process running',
  'completed successfully',
]);

/**
 * R3 / R8 / R9 — placeholder metadata values. Matching is exact whole-value: `test`
 * matches, `test-payments-service` does not.
 */
export const PLACEHOLDER_VALUES = Object.freeze(['unknown', 'test', 'default', 'n/a']);

/**
 * R10 — impact values that restate the technical cause instead of the operational
 * symptom. Deliberately narrow: it is the regex proxy for LLM principle P9, so
 * `high cpu causes checkout latency` continues to the model rather than matching here.
 */
export const R10_TECHNICAL_CAUSE_IMPACTS = Object.freeze([
  'high cpu',
  'high cpu usage',
  'cpu usage is high',
  'cpu is high',
]);

/** Rule set membership (design section 3.6): core rules read v1 and v2 side by side. */
export const CORE_RULE_IDS = Object.freeze(['R1', 'R2', 'R3', 'R4', 'R5', 'R7']);

/** V2 readiness gaps. Never enter `flagged`, never block LLM assessment. */
export const V2_READINESS_RULE_IDS = Object.freeze(['R8', 'R9', 'R10']);

/** R6 (spam volume) is post-MVP and is deliberately absent from both lists. */
export const ALL_RULE_IDS = Object.freeze([...CORE_RULE_IDS, ...V2_READINESS_RULE_IDS]);

/**
 * The LLM principle catalogue (design section 4.1). Disjoint from R1-R10, versioned
 * together with it, and both namespaces are legal citations in a verdict.
 */
export const PRINCIPLE_CATALOG = Object.freeze([
  {
    id: 'P1',
    set: 'core',
    text: 'Non-actionable — implies no investigation, fix, escalation or attention',
  },
  {
    id: 'P2',
    set: 'core',
    text: 'Informational — reports an event or a status rather than a problem ("that\'s a log!")',
  },
  {
    id: 'P3',
    set: 'core',
    text: 'States the outcome, not the failure — something failed, but not what',
  },
  { id: 'P4', set: 'core', text: 'Component or application name does not identify a real thing' },
  { id: 'P5', set: 'core', text: 'No environment context — the reader cannot tell where it fired' },
  {
    id: 'P6',
    set: 'core',
    text: 'Not grounded in a golden signal (latency / traffic / errors / saturation)',
  },
  {
    id: 'P7',
    set: 'v2',
    text: 'critical that fails the Wake-Up Test (urgent + immediate damage + runbook)',
  },
  { id: 'P8', set: 'v2', text: 'Severity is not derived from impact — the two are incoherent' },
  {
    id: 'P9',
    set: 'v2',
    text: 'impact restates the technical cause rather than the operational symptom',
  },
  {
    id: 'P10',
    set: 'core',
    text: 'The required response is robotic and should have been automated, not alerted',
  },
  {
    id: 'P11',
    set: 'core',
    text: 'An internal technical cause with no user-visible symptom anywhere in the alert',
  },
]);

/** Every principle id the model may cite. */
export const PRINCIPLE_IDS = Object.freeze(PRINCIPLE_CATALOG.map((p) => p.id));

/**
 * Ids a verdict may cite for `catalog_violation`: the deterministic rules plus the
 * principles. The model is not confined to R1-R10 — it may cite an R id for something
 * regex missed.
 */
export const CITABLE_IDS = Object.freeze([
  'R1',
  'R2',
  'R3',
  'R4',
  'R5',
  'R6',
  'R7',
  'R8',
  'R9',
  'R10',
  ...PRINCIPLE_IDS,
]);
