import { tokenize, SqlParseError } from './lexer.js';

/**
 * Recursive-descent parser producing the WHERE-clause AST (design section 5.2, blueprint
 * section 2.6).
 *
 * "Build a small parser for the supported query language rather than interpreting query
 * text with string matching": the discriminator between suppression and scoping is which
 * field is negated and where the predicate sits in the boolean tree, and only a real tree
 * can answer the second half. Reading `severity = 'critical'` as suppression would mark a
 * team's entire non-critical inventory as bad alerts.
 *
 * AST node shapes:
 *   {type:'and'|'or', left, right}
 *   {type:'not', operand}
 *   {type:'comparison', field, operator, value, negated}
 *   {type:'in', field, values, negated}
 *   {type:'like', field, pattern, negated}
 *   {type:'is_null', field, negated}
 *   {type:'between', field, low, high, negated}
 *   {type:'call', name, args}          -- $__timeFilter(...) and other macros
 *   {type:'unsupported', text}         -- shape the classifier must not interpret
 *
 * Operand shapes: {kind:'field'|'literal'|'variable'|'call', ...}
 */

export { SqlParseError };

/** Clauses that end the WHERE expression. */
const TERMINATORS = new Set(['GROUP', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'UNION']);

class Parser {
  /** @param {import('./lexer.js').Token[]} tokens */
  constructor(tokens) {
    this.tokens = tokens;
    this.pos = 0;
  }

  peek(offset = 0) {
    return this.tokens[Math.min(this.pos + offset, this.tokens.length - 1)];
  }

  next() {
    return this.tokens[this.pos++];
  }

  /**
   * @param {string} value
   * @returns {boolean}
   */
  matchKeyword(value) {
    const token = this.peek();
    if (token.type === 'keyword' && token.value === value) {
      this.pos += 1;
      return true;
    }
    return false;
  }

  /**
   * @param {string} value
   * @returns {boolean}
   */
  matchPunct(value) {
    const token = this.peek();
    if (token.type === 'punct' && token.value === value) {
      this.pos += 1;
      return true;
    }
    return false;
  }

  /** @param {string} value */
  expectPunct(value) {
    if (!this.matchPunct(value)) {
      throw new SqlParseError(`expected ${JSON.stringify(value)}`, this.peek().start);
    }
  }

  /** @returns {boolean} */
  atExpressionEnd() {
    const token = this.peek();
    if (token.type === 'eof') return true;
    if (token.type === 'punct' && (token.value === ')' || token.value === ';')) return true;
    if (token.type === 'keyword' && TERMINATORS.has(token.value)) return true;
    return false;
  }

  /** @returns {any} */
  parseExpression() {
    return this.parseOr();
  }

  parseOr() {
    let left = this.parseAnd();
    while (this.peek().type === 'keyword' && this.peek().value === 'OR') {
      this.next();
      const right = this.parseAnd();
      left = { type: 'or', left, right };
    }
    return left;
  }

  parseAnd() {
    let left = this.parseNot();
    while (this.peek().type === 'keyword' && this.peek().value === 'AND') {
      this.next();
      const right = this.parseNot();
      left = { type: 'and', left, right };
    }
    return left;
  }

  parseNot() {
    if (this.matchKeyword('NOT')) {
      return { type: 'not', operand: this.parseNot() };
    }
    return this.parsePrimary();
  }

  parsePrimary() {
    if (this.matchPunct('(')) {
      const inner = this.parseExpression();
      this.expectPunct(')');
      return inner;
    }
    return this.parsePredicate();
  }

  /**
   * Parse one operand: a column, a literal, a template variable, or a function call.
   * @returns {any}
   */
  parseOperand() {
    const token = this.next();

    if (token.type === 'identifier') {
      // function call, e.g. LOWER(node_name)
      if (this.peek().type === 'punct' && this.peek().value === '(') {
        const args = this.parseArgumentList();
        return { kind: 'call', name: token.value, args };
      }
      // Strip a table qualifier: `a.node_name` identifies the same column as `node_name`.
      const bare = token.value.includes('.') ? token.value.split('.').pop() : token.value;
      return { kind: 'field', name: /** @type {string} */ (bare), raw: token.value };
    }

    if (token.type === 'variable') {
      if (this.peek().type === 'punct' && this.peek().value === '(') {
        const args = this.parseArgumentList();
        return { kind: 'call', name: token.value, args, macro: true };
      }
      return { kind: 'variable', name: token.value, raw: token.raw };
    }

    if (token.type === 'string')
      return { kind: 'literal', value: token.value, literalType: 'string' };
    if (token.type === 'number')
      return { kind: 'literal', value: token.value, literalType: 'number' };
    if (
      token.type === 'keyword' &&
      (token.value === 'NULL' || token.value === 'TRUE' || token.value === 'FALSE')
    ) {
      return { kind: 'literal', value: token.value, literalType: 'keyword' };
    }

    throw new SqlParseError(
      `unexpected token ${JSON.stringify(token.raw)} in expression`,
      token.start,
    );
  }

  /** @returns {any[]} */
  parseArgumentList() {
    this.expectPunct('(');
    /** @type {any[]} */
    const args = [];
    if (this.matchPunct(')')) return args;
    for (;;) {
      args.push(this.parseOperand());
      if (this.matchPunct(',')) continue;
      this.expectPunct(')');
      return args;
    }
  }

  /** @returns {any} */
  parsePredicate() {
    const left = this.parseOperand();

    // A bare macro such as $__timeFilter(@timestamp) is a complete predicate.
    if (left.kind === 'call' && (this.atExpressionEnd() || this.peek().type === 'keyword')) {
      const token = this.peek();
      if (
        token.type !== 'keyword' ||
        token.value === 'AND' ||
        token.value === 'OR' ||
        TERMINATORS.has(token.value)
      ) {
        return { type: 'call', name: left.name, args: left.args, macro: left.macro === true };
      }
    }

    // IS [NOT] NULL
    if (this.matchKeyword('IS')) {
      const negated = this.matchKeyword('NOT');
      if (!this.matchKeyword('NULL')) {
        throw new SqlParseError('expected NULL after IS', this.peek().start);
      }
      return { type: 'is_null', field: left, negated };
    }

    // [NOT] IN / LIKE / BETWEEN
    let negated = false;
    if (this.peek().type === 'keyword' && this.peek().value === 'NOT') {
      const after = this.peek(1);
      if (after.type === 'keyword' && ['IN', 'LIKE', 'BETWEEN'].includes(after.value)) {
        this.next();
        negated = true;
      }
    }

    if (this.matchKeyword('IN')) {
      const values = this.parseArgumentList();
      return { type: 'in', field: left, values, negated };
    }

    if (this.matchKeyword('LIKE')) {
      const pattern = this.parseOperand();
      return { type: 'like', field: left, pattern, negated };
    }

    if (this.matchKeyword('BETWEEN')) {
      const low = this.parseOperand();
      if (!this.matchKeyword('AND')) {
        throw new SqlParseError('expected AND in BETWEEN', this.peek().start);
      }
      const high = this.parseOperand();
      return { type: 'between', field: left, low, high, negated };
    }

    if (negated) {
      throw new SqlParseError(
        'NOT must be followed by IN, LIKE or BETWEEN here',
        this.peek().start,
      );
    }

    // comparison
    const token = this.peek();
    if (token.type === 'operator') {
      this.next();
      const right = this.parseOperand();
      return { type: 'comparison', field: left, operator: token.value, value: right };
    }

    throw new SqlParseError(
      `unsupported predicate near ${JSON.stringify(token.raw || token.value)}`,
      token.start,
    );
  }
}

/**
 * Parse a panel query and return its WHERE-clause AST.
 *
 * A query with no WHERE clause is valid and simply suppresses nothing.
 *
 * @param {string} sqlText
 * @returns {{where: any|null, hasWhere: boolean}}
 */
export function parsePanelSql(sqlText) {
  const tokens = tokenize(sqlText);

  // Find the top-level WHERE. Depth tracking keeps a subquery's WHERE from being taken
  // for the outer one.
  let depth = 0;
  let whereIndex = -1;
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (token.type === 'punct' && token.value === '(') depth += 1;
    else if (token.type === 'punct' && token.value === ')') depth -= 1;
    else if (token.type === 'keyword' && token.value === 'WHERE' && depth === 0) {
      whereIndex = i;
      break;
    }
  }

  if (whereIndex === -1) return { where: null, hasWhere: false };

  const parser = new Parser(tokens.slice(whereIndex + 1));
  const where = parser.parseExpression();
  if (!parser.atExpressionEnd()) {
    throw new SqlParseError(
      `unparsed input after the WHERE clause near ${JSON.stringify(parser.peek().raw)}`,
      parser.peek().start,
    );
  }
  return { where, hasWhere: true };
}

/**
 * Walk the AST and yield every leaf together with whether it sits inside an `OR` or a
 * `NOT`.
 *
 * This is the rewrite-safety rule from design section 5.2: only suppression leaves that
 * are AND-ed at the top level may be evaluated, because
 * `operator = 'x' AND (node_name != 'junk' OR severity = 'critical')` changes meaning if
 * the leaf is lifted out. A `NOT` wrapper flips the leaf's sense, so its subtree is
 * treated the same way — nested, and therefore unmeasured if it looks like suppression.
 *
 * @param {any} node
 * @param {boolean} [nested]
 * @returns {Array<{leaf: any, nested: boolean}>}
 */
export function collectLeaves(node, nested = false) {
  if (!node) return [];
  if (node.type === 'and') {
    return [...collectLeaves(node.left, nested), ...collectLeaves(node.right, nested)];
  }
  if (node.type === 'or') {
    return [...collectLeaves(node.left, true), ...collectLeaves(node.right, true)];
  }
  if (node.type === 'not') {
    return collectLeaves(node.operand, true);
  }
  return [{ leaf: node, nested }];
}
