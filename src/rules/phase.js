/**
 * Migration phase derivation (design section 3.4; flow step 7).
 *
 * The phase is derived from distinct identity presence and phase-2 readiness, never
 * self-reported: self-reported progress drifts optimistic.
 *
 * `no_data` exists so that an empty seven-day window is not read as proof that a team has
 * not migrated. And v1 reaching zero does not by itself mark a team done — phase-2
 * readiness must also be complete.
 *
 * Like every phase conclusion here, this describes only alerts that fired in the measured
 * week. It cannot see silent rules or an external inventory.
 */

/**
 * @typedef {'no_data'|'phase_0'|'phase_1'|'phase_2'|'done'} Phase
 */

/**
 * @param {number} v1IdentityCount Distinct v1 identities in the window.
 * @param {number} v2IdentityCount Distinct v2 identities in the window.
 * @param {number|null} phase2ReadinessPct Null when there are no v2 identities.
 * @returns {Phase}
 */
export function derivePhase(v1IdentityCount, v2IdentityCount, phase2ReadinessPct) {
  const hasV1 = v1IdentityCount > 0;
  const hasV2 = v2IdentityCount > 0;

  if (!hasV1 && !hasV2) return 'no_data';
  if (hasV1 && !hasV2) return 'phase_0';
  if (hasV1 && hasV2) return 'phase_1';
  // v2 only from here.
  return phase2ReadinessPct !== null && phase2ReadinessPct >= 100 ? 'done' : 'phase_2';
}

/** Human-readable phase labels for the scorecard. */
export const PHASE_LABELS = Object.freeze({
  no_data: 'No data in this window',
  phase_0: 'Phase 0 — Clean',
  phase_1: 'Phase 1 — New rules (dual-run)',
  phase_2: 'Phase 2 — Enrich',
  done: 'Done',
});
