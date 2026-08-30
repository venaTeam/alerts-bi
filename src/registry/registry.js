import { readFileSync } from 'node:fs';
import path from 'node:path';
import ajvModule from 'ajv/dist/2020.js';
import ajvFormatsModule from 'ajv-formats';
import { sha256Text } from '../util/hash.js';

// ajv and ajv-formats are CommonJS. Node hands back the class itself, but the
// interop shape differs under type checking, so normalize once here.
const Ajv = /** @type {any} */ (ajvModule).default ?? /** @type {any} */ (ajvModule);
const addFormats =
  /** @type {any} */ (ajvFormatsModule).default ?? /** @type {any} */ (ajvFormatsModule);

/**
 * Team registry loading and validation (design section 3.1, flow step 1).
 *
 * Ownership is supplied, never inferred. Validation completes before any Elasticsearch
 * query, because a registry mistake silently changes which alerts belong to a team and
 * that error is invisible in the output.
 */

export const DEFAULT_REGISTRY_PATH = path.join('config', 'teams.json');
const SCHEMA_PATH = path.join('config', 'teams.schema.json');

/**
 * @typedef {object} PanelVariable
 * @property {string} name
 * @property {'custom'|'constant'|'interval'|'query'} type
 * @property {string} [value]
 * @property {string[]} [values]
 * @property {boolean} [multi]
 * @property {boolean} [all_selected]
 */

/**
 * @typedef {object} Panel
 * @property {string} panel_id
 * @property {'v1'|'v2'} schema
 * @property {string} sql
 * @property {PanelVariable[]} [variables]
 */

/**
 * @typedef {object} TeamEntry
 * @property {string} team_id
 * @property {string} display_name
 * @property {string[]} v1_operators
 * @property {string|null} v2_operator
 * @property {Panel[]} [panels]
 */

/**
 * @typedef {object} Registry
 * @property {string} registry_version
 * @property {string} [effective_date]
 * @property {TeamEntry[]} teams
 */

/**
 * @typedef {object} LoadedRegistry
 * @property {Registry} registry
 * @property {string} registryVersion
 * @property {string} fileSha256 SHA-256 of the complete registry document as read.
 * @property {string} filePath
 */

/** Raised for any registry problem. Callers fail the run before querying alerts. */
export class RegistryError extends Error {
  /**
   * @param {string} message
   * @param {string[]} [details]
   */
  constructor(message, details = []) {
    super(details.length ? `${message}\n  - ${details.join('\n  - ')}` : message);
    this.name = 'RegistryError';
    this.details = details;
  }
}

/**
 * Validate a parsed registry document against the checked-in JSON Schema plus the
 * cross-entry rules the schema cannot express.
 *
 * @param {unknown} doc
 * @returns {Registry}
 */
export function validateRegistry(doc) {
  const schema = JSON.parse(readFileSync(SCHEMA_PATH, 'utf8'));
  const ajv = new Ajv({ allErrors: true, strict: true });
  addFormats(ajv);
  const validate = ajv.compile(schema);

  if (!validate(doc)) {
    const details = (validate.errors || []).map(
      (e) =>
        `${e.instancePath || '/'} ${e.message}${e.params ? ` ${JSON.stringify(e.params)}` : ''}`,
    );
    throw new RegistryError('registry does not match config/teams.schema.json', details);
  }

  const registry = /** @type {Registry} */ (doc);
  /** @type {string[]} */
  const problems = [];

  /** @type {Map<string, string>} team_id -> first occurrence */
  const seenTeamIds = new Map();
  /** Exact, case-sensitive operator value -> owning team_id (design section 3.1). */
  const seenOperators = new Map();

  for (const team of registry.teams) {
    if (seenTeamIds.has(team.team_id)) {
      problems.push(`duplicate team_id "${team.team_id}"`);
    }
    seenTeamIds.set(team.team_id, team.team_id);

    // "at least one source operator must exist" — an entry with neither cannot be
    // queried at all, and silently returning zero alerts would read as a clean team.
    if (team.v1_operators.length === 0 && team.v2_operator === null) {
      problems.push(
        `team "${team.team_id}" has no source operator: v1_operators is empty and v2_operator is null`,
      );
    }

    // Uniqueness is a CROSS-team rule. One team may legitimately carry the same string
    // as both a v1 operator and its v2 operator (a team that kept its name through the
    // migration), so the same value seen twice inside one entry is not a conflict.
    const own = new Set([
      ...team.v1_operators,
      ...(team.v2_operator === null ? [] : [team.v2_operator]),
    ]);
    for (const op of own) {
      const owner = seenOperators.get(op);
      if (owner !== undefined && owner !== team.team_id) {
        problems.push(
          `operator "${op}" is claimed by both "${owner}" and "${team.team_id}" (matching is exact and case-sensitive)`,
        );
      } else {
        seenOperators.set(op, team.team_id);
      }
    }

    /** @type {Set<string>} */
    const panelIds = new Set();
    for (const panel of team.panels || []) {
      if (panelIds.has(panel.panel_id)) {
        problems.push(`team "${team.team_id}" has duplicate panel_id "${panel.panel_id}"`);
      }
      panelIds.add(panel.panel_id);

      /** @type {Set<string>} */
      const varNames = new Set();
      for (const v of panel.variables || []) {
        if (varNames.has(v.name)) {
          problems.push(
            `panel "${panel.panel_id}" of team "${team.team_id}" defines variable "${v.name}" twice`,
          );
        }
        varNames.add(v.name);
        if ((v.type === 'constant' || v.type === 'interval') && v.value === undefined) {
          problems.push(
            `panel "${panel.panel_id}" variable "${v.name}" is ${v.type} but has no "value"`,
          );
        }
        if (v.type === 'custom' && v.values === undefined) {
          problems.push(
            `panel "${panel.panel_id}" variable "${v.name}" is custom but has no "values"`,
          );
        }
      }
    }
  }

  if (problems.length) throw new RegistryError('registry validation failed', problems);
  return registry;
}

/**
 * Read, hash and validate the registry document.
 *
 * The hash covers the complete file bytes rather than the parsed object: a run must be
 * able to prove exactly which document it used, including entries for teams it did not
 * select.
 *
 * @param {string} [filePath]
 * @returns {LoadedRegistry}
 */
export function loadRegistry(filePath = DEFAULT_REGISTRY_PATH) {
  /** @type {string} */
  let raw;
  try {
    raw = readFileSync(filePath, 'utf8');
  } catch (err) {
    throw new RegistryError(
      `cannot read registry at ${filePath}: ${/** @type {any} */ (err).message}`,
    );
  }

  /** @type {unknown} */
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new RegistryError(
      `registry at ${filePath} is not valid JSON: ${/** @type {any} */ (err).message}`,
    );
  }

  const registry = validateRegistry(parsed);
  return {
    registry,
    registryVersion: registry.registry_version,
    fileSha256: sha256Text(raw),
    filePath,
  };
}

/**
 * Select exactly one team. A run never defaults to all teams (design section 6).
 *
 * @param {LoadedRegistry} loaded
 * @param {string} teamId
 * @returns {TeamEntry}
 */
export function selectTeam(loaded, teamId) {
  const team = loaded.registry.teams.find((t) => t.team_id === teamId);
  if (!team) {
    const known = loaded.registry.teams
      .map((t) => t.team_id)
      .sort()
      .join(', ');
    throw new RegistryError(`team "${teamId}" is not in the registry. Known teams: ${known}`);
  }
  return team;
}

/**
 * Immutable snapshot of the selected entry, stored with the run so the ownership used is
 * reproducible even after the registry is edited.
 *
 * @param {TeamEntry} team
 * @returns {string}
 */
export function snapshotTeamEntry(team) {
  return JSON.stringify(team);
}
