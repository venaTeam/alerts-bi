import { logger, redactError } from '../util/logger.js';

/**
 * Minimal Elasticsearch HTTP client.
 *
 * Uses `fetch` and the same request shape as the existing mock scripts rather than
 * pulling in the official client: the pipeline issues three request types (open PIT,
 * search, close PIT) and nothing else, and a thin client keeps the exact request body
 * visible in one place.
 *
 * TLS note: for an on-prem cluster with a private CA, point `NODE_EXTRA_CA_CERTS` at the
 * bundle. `fetch` has no per-request CA option, so a config field would be silently
 * ignored, which is worse than not offering one.
 */

export class ElasticsearchError extends Error {
  /**
   * @param {string} message
   * @param {number} [status]
   * @param {string} [body]
   */
  constructor(message, status, body) {
    super(message);
    this.name = 'ElasticsearchError';
    this.status = status;
    this.body = body;
  }
}

export class EsClient {
  /**
   * @param {import('../config/env.js').EsConfig} config
   */
  constructor(config) {
    this.config = config;
    /** @type {Record<string, string>} */
    this.headers = { 'Content-Type': 'application/json' };
    if (config.username) {
      const token = Buffer.from(`${config.username}:${config.password}`).toString('base64');
      this.headers.Authorization = `Basic ${token}`;
    }
  }

  /**
   * @param {string} method
   * @param {string} path
   * @param {unknown} [body]
   * @returns {Promise<any>}
   */
  async request(method, path, body) {
    const url = `${this.config.url}${path}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.config.requestTimeoutMs);
    try {
      const res = await fetch(url, {
        method,
        headers: this.headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await res.text();
      if (!res.ok) {
        // The response body can echo query fragments, so it is kept on the error object
        // for the caller to handle but never logged wholesale.
        throw new ElasticsearchError(
          `elasticsearch ${method} ${path} failed with ${res.status}`,
          res.status,
          text.slice(0, 2000),
        );
      }
      return text ? JSON.parse(text) : null;
    } catch (err) {
      if (err instanceof ElasticsearchError) throw err;
      if (/** @type {any} */ (err)?.name === 'AbortError') {
        throw new ElasticsearchError(
          `elasticsearch ${method} ${path} timed out after ${this.config.requestTimeoutMs}ms`,
        );
      }
      throw new ElasticsearchError(`elasticsearch ${method} ${path} failed: ${redactError(err)}`);
    } finally {
      clearTimeout(timer);
    }
  }

  /**
   * Open a point-in-time so pagination sees one consistent view of the index.
   *
   * Without a PIT, `from`/`size` paging over a live index can skip or repeat documents as
   * segments merge — which would make `alerts` and `distinct_alerts` depend on how busy
   * the cluster was during the run.
   *
   * @param {string} index
   * @param {string} [keepAlive]
   * @returns {Promise<string>}
   */
  async openPit(index, keepAlive = '2m') {
    const res = await this.request('POST', `/${index}/_pit?keep_alive=${keepAlive}`);
    if (!res?.id) throw new ElasticsearchError(`could not open a point-in-time on ${index}`);
    return res.id;
  }

  /**
   * @param {string} pitId
   * @returns {Promise<void>}
   */
  async closePit(pitId) {
    try {
      await this.request('DELETE', '/_pit', { id: pitId });
    } catch (err) {
      // A leaked PIT expires on its own; failing the run over cleanup would discard a
      // complete, correct result.
      logger.warn('es.pit_close_failed', { error: redactError(err) });
    }
  }

  /**
   * @param {unknown} body
   * @returns {Promise<any>}
   */
  async search(body) {
    return this.request('POST', '/_search', body);
  }

  /**
   * @param {string} index
   * @returns {Promise<boolean>}
   */
  async indexExists(index) {
    try {
      await this.request('GET', `/${index}`);
      return true;
    } catch (err) {
      if (err instanceof ElasticsearchError && err.status === 404) return false;
      throw err;
    }
  }
}
