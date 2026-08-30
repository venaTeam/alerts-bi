/**
 * Tokenizer for the panel-query subset (design section 5.2).
 *
 * A purpose-built lexer rather than a general SQL library: the queries carry Grafana
 * template variables (`$nodes`, `${nodes}`, `[[nodes]]`, `$__timeFilter(...)`) that a
 * standard SQL grammar rejects outright, and the classification this step performs is a
 * lookup over field names and operators rather than full SQL semantics.
 *
 * Anything the lexer cannot represent makes the parse fail, and a parse failure produces
 * `suppression_unmeasured` — never an assumed suppression.
 */

/**
 * @typedef {'identifier'|'string'|'number'|'operator'|'punct'|'keyword'|'variable'|'eof'} TokenType
 */

/**
 * @typedef {object} Token
 * @property {TokenType} type
 * @property {string} value Normalized value: keywords uppercased, strings unquoted.
 * @property {string} raw Source text exactly as written.
 * @property {number} start
 */

export class SqlParseError extends Error {
  /**
   * @param {string} message
   * @param {number} [position]
   */
  constructor(message, position) {
    super(position === undefined ? message : `${message} (at offset ${position})`);
    this.name = 'SqlParseError';
    this.position = position;
  }
}

/** Words that must not be read as column names. */
const KEYWORDS = new Set([
  'SELECT',
  'FROM',
  'WHERE',
  'AND',
  'OR',
  'NOT',
  'IN',
  'LIKE',
  'IS',
  'NULL',
  'BETWEEN',
  'GROUP',
  'ORDER',
  'BY',
  'HAVING',
  'LIMIT',
  'OFFSET',
  'AS',
  'JOIN',
  'INNER',
  'LEFT',
  'RIGHT',
  'FULL',
  'OUTER',
  'ON',
  'UNION',
  'DISTINCT',
  'TOP',
  'CASE',
  'WHEN',
  'THEN',
  'ELSE',
  'END',
  'ASC',
  'DESC',
  'TRUE',
  'FALSE',
]);

/** Multi-character operators, longest first so `<=` is not read as `<` then `=`. */
const OPERATORS = ['<=', '>=', '<>', '!=', '!<', '!>', '=', '<', '>'];

/**
 * @param {string} sqlText
 * @returns {Token[]}
 */
export function tokenize(sqlText) {
  /** @type {Token[]} */
  const tokens = [];
  let i = 0;
  const n = sqlText.length;

  while (i < n) {
    const ch = sqlText[i];

    // whitespace
    if (/\s/.test(ch)) {
      i += 1;
      continue;
    }

    // line comment
    if (ch === '-' && sqlText[i + 1] === '-') {
      while (i < n && sqlText[i] !== '\n') i += 1;
      continue;
    }

    // block comment
    if (ch === '/' && sqlText[i + 1] === '*') {
      const end = sqlText.indexOf('*/', i + 2);
      if (end === -1) throw new SqlParseError('unterminated block comment', i);
      i = end + 2;
      continue;
    }

    // single-quoted string; '' is an escaped quote
    if (ch === "'") {
      const start = i;
      i += 1;
      let value = '';
      for (;;) {
        if (i >= n) throw new SqlParseError('unterminated string literal', start);
        if (sqlText[i] === "'") {
          if (sqlText[i + 1] === "'") {
            value += "'";
            i += 2;
            continue;
          }
          i += 1;
          break;
        }
        value += sqlText[i];
        i += 1;
      }
      tokens.push({ type: 'string', value, raw: sqlText.slice(start, i), start });
      continue;
    }

    // bracketed or double-quoted identifier
    if (ch === '[' && sqlText[i + 1] !== '[') {
      const end = sqlText.indexOf(']', i + 1);
      if (end === -1) throw new SqlParseError('unterminated [identifier]', i);
      tokens.push({
        type: 'identifier',
        value: sqlText.slice(i + 1, end),
        raw: sqlText.slice(i, end + 1),
        start: i,
      });
      i = end + 1;
      continue;
    }
    if (ch === '"') {
      const end = sqlText.indexOf('"', i + 1);
      if (end === -1) throw new SqlParseError('unterminated "identifier"', i);
      tokens.push({
        type: 'identifier',
        value: sqlText.slice(i + 1, end),
        raw: sqlText.slice(i, end + 1),
        start: i,
      });
      i = end + 1;
      continue;
    }

    // Grafana variable: [[name]]
    if (ch === '[' && sqlText[i + 1] === '[') {
      const end = sqlText.indexOf(']]', i + 2);
      if (end === -1) throw new SqlParseError('unterminated [[variable]]', i);
      tokens.push({
        type: 'variable',
        value: sqlText.slice(i + 2, end).trim(),
        raw: sqlText.slice(i, end + 2),
        start: i,
      });
      i = end + 2;
      continue;
    }

    // Grafana variable: ${name} or ${name:format}
    if (ch === '$' && sqlText[i + 1] === '{') {
      const end = sqlText.indexOf('}', i + 2);
      if (end === -1) throw new SqlParseError('unterminated ${variable}', i);
      const inner = sqlText.slice(i + 2, end).trim();
      tokens.push({
        type: 'variable',
        // A format suffix (${nodes:csv}) selects rendering, not identity.
        value: inner.split(':')[0].trim(),
        raw: sqlText.slice(i, end + 1),
        start: i,
      });
      i = end + 1;
      continue;
    }

    // Grafana variable or macro: $name / $__timeFilter
    if (ch === '$') {
      const match = /^\$[A-Za-z_][A-Za-z0-9_]*/.exec(sqlText.slice(i));
      if (!match) throw new SqlParseError('stray $ in query', i);
      tokens.push({
        type: 'variable',
        value: match[0].slice(1),
        raw: match[0],
        start: i,
      });
      i += match[0].length;
      continue;
    }

    // number
    if (/[0-9]/.test(ch)) {
      const match = /^[0-9]+(\.[0-9]+)?/.exec(sqlText.slice(i));
      const raw = /** @type {RegExpExecArray} */ (match)[0];
      tokens.push({ type: 'number', value: raw, raw, start: i });
      i += raw.length;
      continue;
    }

    // identifier or keyword
    if (/[A-Za-z_@#]/.test(ch)) {
      const match = /^[A-Za-z_@#][A-Za-z0-9_@#$.]*/.exec(sqlText.slice(i));
      const raw = /** @type {RegExpExecArray} */ (match)[0];
      const upper = raw.toUpperCase();
      tokens.push({
        type: KEYWORDS.has(upper) ? 'keyword' : 'identifier',
        value: KEYWORDS.has(upper) ? upper : raw,
        raw,
        start: i,
      });
      i += raw.length;
      continue;
    }

    // operators
    const operator = OPERATORS.find((op) => sqlText.startsWith(op, i));
    if (operator) {
      tokens.push({ type: 'operator', value: operator, raw: operator, start: i });
      i += operator.length;
      continue;
    }

    // punctuation
    if ('(),;*'.includes(ch)) {
      tokens.push({ type: 'punct', value: ch, raw: ch, start: i });
      i += 1;
      continue;
    }

    throw new SqlParseError(`unexpected character ${JSON.stringify(ch)}`, i);
  }

  tokens.push({ type: 'eof', value: '', raw: '', start: n });
  return tokens;
}
