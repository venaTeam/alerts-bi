/**
 * The `LlmClient` contract.
 *
 * One method, deliberately: the pipeline owns the three-attempt policy, the batch
 * identity, the validation and the persistence, and a client that could retry or
 * reorder on its own would make those guarantees untrue.
 *
 * A client returns the raw response TEXT. Parsing and validation happen in the pipeline
 * so the real adapter and the deterministic fake are held to identical standards.
 *
 * @typedef {object} LlmClient
 * @property {(args: {systemPrompt: string, requestText: string, batchId: string, attempt: number}) => Promise<string>} complete
 * @property {string} modelVersion
 */

export class LlmTransportError extends Error {
  /**
   * @param {string} message
   * @param {string} [kind]
   */
  constructor(message, kind = 'transport') {
    super(message);
    this.name = 'LlmTransportError';
    this.kind = kind;
  }
}
