/**
 * Grafana template-variable resolution from the frozen registry snapshot
 * (design section 5.2).
 *
 * The MVP never calls Grafana. The standardization team supplies each panel's variable
 * definitions alongside the SQL, which removes live configuration drift from a
 * reproducible run.
 *
 * Only `custom`, `constant` and `interval` resolve. A `query` variable is never executed,
 * and a missing required definition is never guessed: either condition makes the
 * suppression leaf PRESENT BUT UNMEASURED, counted in `suppression_unmeasured` rather
 * than silently dropped, so an under-reported number is visible as under-reported instead
 * of passing for zero.
 */

/**
 * @typedef {import('../registry/registry.js').PanelVariable} PanelVariable
 */

/**
 * @typedef {object} ResolvedVariable
 * @property {boolean} resolved
 * @property {string[]} values Empty when unresolved.
 * @property {boolean} allSelected True when the panel had "all" selected.
 * @property {string} [reason] Why it could not be resolved.
 */

/**
 * @param {string} name
 * @param {PanelVariable[]} definitions
 * @returns {ResolvedVariable}
 */
export function resolveVariable(name, definitions) {
  const definition = definitions.find((d) => d.name === name);

  if (!definition) {
    return {
      resolved: false,
      values: [],
      allSelected: false,
      reason: `no frozen definition supplied for variable "${name}"`,
    };
  }

  if (definition.type === 'query') {
    return {
      resolved: false,
      values: [],
      allSelected: false,
      reason: `variable "${name}" is a query variable and is never executed`,
    };
  }

  if (definition.type === 'constant' || definition.type === 'interval') {
    if (typeof definition.value !== 'string') {
      return {
        resolved: false,
        values: [],
        allSelected: false,
        reason: `variable "${name}" is ${definition.type} but supplies no value`,
      };
    }
    return { resolved: true, values: [definition.value], allSelected: false };
  }

  // custom: the complete selected-value list, plus whether "all" was selected.
  if (!Array.isArray(definition.values)) {
    return {
      resolved: false,
      values: [],
      allSelected: false,
      reason: `variable "${name}" is custom but supplies no values`,
    };
  }
  return {
    resolved: true,
    values: [...definition.values],
    // The $__all case: a multi-value variable expanding to everything is exactly what the
    // blast-radius guard exists to catch, so it is resolved and then measured rather than
    // rejected here.
    allSelected: definition.all_selected === true,
  };
}

/**
 * Resolve an operand to the concrete set of values it stands for.
 *
 * @param {any} operand Parser operand node.
 * @param {PanelVariable[]} definitions
 * @returns {{resolved: boolean, values: string[], allSelected: boolean, reason?: string}}
 */
export function resolveOperand(operand, definitions) {
  if (!operand)
    return { resolved: false, values: [], allSelected: false, reason: 'missing operand' };

  if (operand.kind === 'literal') {
    return { resolved: true, values: [String(operand.value)], allSelected: false };
  }

  if (operand.kind === 'variable') {
    return resolveVariable(operand.name, definitions);
  }

  if (operand.kind === 'field') {
    // A column-to-column comparison is not a value-based exclusion the team wrote to hide
    // specific alerts, so it is not interpreted.
    return {
      resolved: false,
      values: [],
      allSelected: false,
      reason: 'comparison against another column is not interpreted',
    };
  }

  return {
    resolved: false,
    values: [],
    allSelected: false,
    reason: `operand of kind "${operand.kind}" is not interpreted`,
  };
}
