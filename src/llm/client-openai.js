import OpenAI from 'openai';
import { LlmTransportError } from './client.js';
import { responseJsonSchema } from './response.js';

/**
 * On-prem OpenAI-compatible adapter (design section 5.1; flow step 6.3).
 *
 * The regular OpenAI Node SDK against the on-prem baseURL, using Chat Completions with
 * strict JSON-schema output and temperature zero.
 *
 * SDK automatic retries are disabled (`maxRetries: 0`). This is not a preference: the
 * pipeline owns a three-attempt policy and records every attempt, and hidden SDK retries
 * would silently exceed it and break the audit trail. A timeout or transport error
 * consumes one recorded attempt like any other failed call.
 */

export class OpenAiLlmClient {
  /**
   * @param {import('../config/env.js').LlmConfig} config
   */
  constructor(config) {
    if (!config.baseUrl) throw new Error('LLM_BASE_URL is required to call the model');
    if (!config.model) throw new Error('LLM_MODEL is required to call the model');
    this.config = config;
    this.modelVersion = config.model;
    this.client = new OpenAI({
      baseURL: config.baseUrl,
      apiKey: config.apiKey || 'not-used',
      timeout: config.timeoutMs,
      maxRetries: 0,
    });
  }

  /**
   * @param {{systemPrompt: string, requestText: string, batchId: string, attempt: number}} args
   * @returns {Promise<string>}
   */
  async complete({ systemPrompt, requestText }) {
    /** @type {any} */
    let completion;
    try {
      completion = await this.client.chat.completions.create({
        model: this.config.model,
        temperature: 0,
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: requestText },
        ],
        response_format: {
          type: 'json_schema',
          json_schema: {
            name: 'alert_batch_verdicts',
            strict: true,
            schema: responseJsonSchema(),
          },
        },
      });
    } catch (err) {
      const e = /** @type {any} */ (err);
      const kind = e?.name === 'APIConnectionTimeoutError' ? 'timeout' : 'transport';
      throw new LlmTransportError(
        `${e?.name || 'Error'}: ${String(e?.message).slice(0, 300)}`,
        kind,
      );
    }

    const content = completion?.choices?.[0]?.message?.content;
    if (typeof content !== 'string' || content.trim() === '') {
      throw new LlmTransportError('model returned an empty message', 'empty');
    }
    return content;
  }
}
