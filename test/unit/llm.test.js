import test from 'node:test';
import assert from 'node:assert/strict';
import {
  groupAlerts,
  balancedPartitionSizes,
  buildBatches,
  alertTransportId,
  batchIdOf,
  MAX_BATCH_SIZE,
} from '../../src/llm/grouping.js';
import {
  buildRequest,
  sharedFieldNames,
  reconstructDocument,
  assertLossless,
  serializeRequest,
} from '../../src/llm/request.js';
import {
  validateResponse,
  stateForVerdict,
  responseJsonSchema,
  LlmResponseError,
} from '../../src/llm/response.js';
import { FakeLlmClient, scriptedVerdicts } from '../../src/llm/client-fake.js';
import { assessAlerts, markAllUnassessed, MAX_ATTEMPTS } from '../../src/llm/assess.js';
import { verdictKey } from '../../src/db/repositories.js';
import { buildPrompt } from '../../src/llm/prompt.js';
import { v1Row, v2Row } from '../helpers/rows.js';

const VERSIONS = { rulesetVersion: '1.0.0', promptVersion: '1.0.0' };
const RUN = { runId: 'run-1', promptVersion: '1.0.0', modelVersion: 'fake-model-1' };

// ------------------------------------------------------------------ grouping

test('alerts group by alert_rule_url when it is present', () => {
  const groups = groupAlerts([
    v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1' }),
    v1Row({ key_field: 'b', alert_rule_url: 'https://g/d/1' }),
    v1Row({ key_field: 'c', alert_rule_url: 'https://g/d/2' }),
  ]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].type, 'alert_rule_url');
  assert.equal(groups[0].alerts.length, 2);
});

test('alerts with no rule URL fall back to application grouping', () => {
  const groups = groupAlerts([
    v1Row({ key_field: 'a', provider: 'api', alert_rule_url: null, application: 'app-x' }),
    v1Row({ key_field: 'b', provider: 'api', alert_rule_url: '   ', application: 'app-x' }),
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].type, 'application');
  assert.equal(groups[0].value, 'app-x');
});

test('rule-URL and application groups never merge', () => {
  const groups = groupAlerts([
    v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1', application: 'app-x' }),
    v1Row({ key_field: 'b', alert_rule_url: null, application: 'app-x' }),
  ]);
  assert.equal(groups.length, 2);
});

test('small groups are never packed together to fill capacity', () => {
  const batches = buildBatches(
    [
      v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1' }),
      v1Row({ key_field: 'b', alert_rule_url: 'https://g/d/2' }),
      v1Row({ key_field: 'c', alert_rule_url: 'https://g/d/3' }),
    ],
    RUN,
  );
  assert.equal(batches.length, 3);
  assert.deepEqual(
    batches.map((b) => b.alerts.length),
    [1, 1, 1],
  );
});

test('grouping and ordering are deterministic regardless of input order', () => {
  const alerts = [
    v1Row({ key_field: 'c', alert_rule_url: 'https://g/d/2' }),
    v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1' }),
    v1Row({ key_field: 'b', alert_rule_url: 'https://g/d/1' }),
  ];
  const forward = buildBatches(alerts, RUN);
  const reversed = buildBatches([...alerts].reverse(), RUN);
  assert.deepEqual(
    forward.map((b) => b.batchId),
    reversed.map((b) => b.batchId),
  );
  assert.deepEqual(
    forward[0].alerts.map((a) => a.keyField),
    ['a', 'b'],
  );
});

// ------------------------------------------------------ balanced partitions

test('401 alerts split into 134, 134 and 133, not 200, 200 and 1', () => {
  assert.deepEqual(balancedPartitionSizes(401, 200), [134, 134, 133]);
});

test('a group at or below the cap is exactly one batch', () => {
  assert.deepEqual(balancedPartitionSizes(200, 200), [200]);
  assert.deepEqual(balancedPartitionSizes(1, 200), [1]);
});

test('partition sizes never differ by more than one and always sum to n', () => {
  for (const n of [201, 399, 400, 401, 599, 1000, 1234]) {
    const sizes = balancedPartitionSizes(n, 200);
    assert.equal(
      sizes.reduce((a, b) => a + b, 0),
      n,
      `sum for ${n}`,
    );
    assert.ok(Math.max(...sizes) - Math.min(...sizes) <= 1, `spread for ${n}`);
    assert.equal(sizes.length, Math.ceil(n / 200), `count for ${n}`);
    assert.ok(Math.max(...sizes) <= 200, `cap for ${n}`);
  }
});

test('a group above the cap becomes balanced batches carrying every alert once', () => {
  const alerts = Array.from({ length: 401 }, (_, i) =>
    v1Row({ key_field: `k${String(i).padStart(4, '0')}`, alert_rule_url: 'https://g/d/big' }),
  );
  const batches = buildBatches(alerts, RUN);
  assert.deepEqual(
    batches.map((b) => b.alerts.length),
    [134, 134, 133],
  );
  assert.equal(
    batches.every((b) => b.partitionCount === 3),
    true,
  );
  const seen = new Set(batches.flatMap((b) => b.alerts.map((a) => a.keyField)));
  assert.equal(seen.size, 401);
});

test('the configured batch size can lower the ceiling but never raise it', () => {
  const alerts = Array.from({ length: 10 }, (_, i) =>
    v1Row({ key_field: `k${i}`, alert_rule_url: 'https://g/d/1' }),
  );
  assert.equal(buildBatches(alerts, { ...RUN, maxBatchSize: 4 }).length, 3);
  assert.equal(buildBatches(alerts, { ...RUN, maxBatchSize: 100000 }).length, 1);
  assert.equal(MAX_BATCH_SIZE, 200);
});

// ------------------------------------------------------------ transport ids

test('the alert transport id is derived from schema, application and key_field', () => {
  const a = alertTransportId(v1Row({ application: 'x', key_field: 'k' }));
  const b = alertTransportId(v1Row({ application: 'x', key_field: 'k', message: 'different' }));
  const c = alertTransportId(v1Row({ application: 'x', key_field: 'k2' }));
  assert.equal(a, b, 'message is not part of transport identity');
  assert.notEqual(a, c);
  assert.equal(a.length, 64);
});

test('the same alert under v1 and v2 gets different transport ids', () => {
  assert.notEqual(
    alertTransportId(v1Row({ application: 'x', key_field: 'k' })),
    alertTransportId(v2Row({ application: 'x', key_field: 'k' })),
  );
});

test('the batch id covers run, group, partition, membership and versions', () => {
  const base = {
    runId: 'r1',
    groupType: 'alert_rule_url',
    groupValue: 'https://g/d/1',
    partitionIndex: 0,
    alertIds: ['a', 'b'],
    promptVersion: '1.0.0',
    modelVersion: 'm1',
  };
  const id = batchIdOf(base);
  assert.equal(batchIdOf({ ...base }), id, 'identical input gives an identical id');
  assert.notEqual(batchIdOf({ ...base, partitionIndex: 1 }), id);
  assert.notEqual(batchIdOf({ ...base, alertIds: ['b', 'a'] }), id, 'ordering is part of identity');
  assert.notEqual(batchIdOf({ ...base, promptVersion: '2.0.0' }), id);
  assert.notEqual(batchIdOf({ ...base, modelVersion: 'm2' }), id);
});

// ------------------------------------------------------------ payload factoring

test('only fields identical across the whole batch are factored out', () => {
  const batch = buildBatches(
    [
      v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1', object: 'same', message: 'one' }),
      v1Row({ key_field: 'b', alert_rule_url: 'https://g/d/1', object: 'same', message: 'two' }),
    ],
    RUN,
  )[0];
  const request = buildRequest(batch, VERSIONS);

  assert.equal(request.shared_fields.object, 'same');
  assert.equal('message' in request.shared_fields, false, 'differing fields stay per-alert');
  assert.equal(request.alerts[0].fields.message, 'one');
});

test('grouping by rule URL does not assume other fields are identical', () => {
  const batch = buildBatches(
    [
      v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1', severity: 'error' }),
      v1Row({ key_field: 'b', alert_rule_url: 'https://g/d/1', severity: 'major' }),
    ],
    RUN,
  )[0];
  const request = buildRequest(batch, VERSIONS);
  assert.equal('severity' in request.shared_fields, false);
});

test('a field present in only some documents is never shared', () => {
  const shared = sharedFieldNames([{ a: 1, b: 2 }, { a: 1 }]);
  assert.deepEqual([...shared], ['a']);
});

test('factoring is lossless: header plus row rebuilds the exact document', () => {
  const alerts = [
    v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1', node_name: 'n1' }),
    v2Row({ key_field: 'b', alert_rule_url: 'https://g/d/1', impact: null, site: 'dc-1' }),
  ];
  for (const batch of buildBatches(alerts, RUN)) {
    const request = buildRequest(batch, VERSIONS);
    assert.doesNotThrow(() => assertLossless(request, batch));
    request.alerts.forEach((alert, i) => {
      assert.deepEqual(reconstructDocument(request, alert), batch.alerts[i].source);
    });
  }
});

test('schema always stays on the alert envelope', () => {
  const batch = buildBatches([v1Row({ key_field: 'a' })], RUN)[0];
  const request = buildRequest(batch, VERSIONS);
  assert.equal(request.alerts[0].schema, 'v1');
  assert.equal('schema' in request.shared_fields, false);
});

test('the serialized payload is stable for identical input', () => {
  const batch = buildBatches([v1Row({ key_field: 'a' })], RUN)[0];
  const a = serializeRequest(buildRequest(batch, VERSIONS));
  const b = serializeRequest(buildRequest(batch, VERSIONS));
  assert.equal(a.text, b.text);
  assert.equal(a.hash, b.hash);
});

// ------------------------------------------------------- response validation

/** @param {any[]} verdicts */
const response = (batchId, verdicts) => ({ batch_id: batchId, verdicts });
const goodVerdict = (id) => ({
  alert_id: id,
  assessment: 'no_violation',
  principle_id: 'NONE',
  confidence: 'high',
  justification: 'ok',
});
const REQ = { batchId: 'b1', alertIds: ['a1', 'a2'] };

test('a valid response returns verdicts in request order', () => {
  const verdicts = validateResponse(response('b1', [goodVerdict('a2'), goodVerdict('a1')]), REQ);
  assert.deepEqual(
    verdicts.map((v) => v.alert_id),
    ['a1', 'a2'],
  );
});

test('a mismatched batch_id rejects the whole response', () => {
  assert.throws(
    () => validateResponse(response('other', [goodVerdict('a1'), goodVerdict('a2')]), REQ),
    /batch_id does not match/,
  );
});

test('a missing verdict rejects the whole response', () => {
  assert.throws(
    () => validateResponse(response('b1', [goodVerdict('a1')]), REQ),
    /missing a verdict/,
  );
});

test('an extra verdict for an unknown alert rejects the whole response', () => {
  assert.throws(
    () =>
      validateResponse(
        response('b1', [goodVerdict('a1'), goodVerdict('a2'), goodVerdict('a3')]),
        REQ,
      ),
    /unknown alert/,
  );
});

test('a duplicated alert id rejects the whole response', () => {
  assert.throws(
    () => validateResponse(response('b1', [goodVerdict('a1'), goodVerdict('a1')]), REQ),
    /duplicate verdict/,
  );
});

test('an unexpected field anywhere rejects the whole response', () => {
  assert.throws(
    () =>
      validateResponse(
        { batch_id: 'b1', verdicts: [goodVerdict('a1'), goodVerdict('a2')], extra: 1 },
        REQ,
      ),
    /unexpected top-level field/,
  );
  assert.throws(
    () =>
      validateResponse(
        response('b1', [{ ...goodVerdict('a1'), score: 0.9 }, goodVerdict('a2')]),
        REQ,
      ),
    /unexpected field "score"/,
  );
});

test('invalid enum values are rejected', () => {
  assert.throws(
    () =>
      validateResponse(
        response('b1', [{ ...goodVerdict('a1'), assessment: 'maybe' }, goodVerdict('a2')]),
        REQ,
      ),
    /assessment .* is not valid/,
  );
  assert.throws(
    () =>
      validateResponse(
        response('b1', [{ ...goodVerdict('a1'), confidence: 0.9 }, goodVerdict('a2')]),
        REQ,
      ),
    /confidence .* is not valid/,
  );
});

test('the assessment/principle pairing is closed in all three directions', () => {
  const cases = [
    [
      { assessment: 'no_violation', principle_id: 'P1' },
      /no_violation must carry principle_id NONE/,
    ],
    [{ assessment: 'other', principle_id: 'P1' }, /other must carry principle_id OTHER/],
    [{ assessment: 'catalog_violation', principle_id: 'NONE' }, /not in the catalogue/],
    [{ assessment: 'catalog_violation', principle_id: 'P99' }, /not in the catalogue/],
  ];
  for (const [patch, pattern] of cases) {
    assert.throws(
      () =>
        validateResponse(
          response('b1', [{ ...goodVerdict('a1'), ...patch }, goodVerdict('a2')]),
          REQ,
        ),
      pattern,
      JSON.stringify(patch),
    );
  }
});

test('the model may cite an R id for something the deterministic rules missed', () => {
  const verdicts = validateResponse(
    response('b1', [
      { ...goodVerdict('a1'), assessment: 'catalog_violation', principle_id: 'R2' },
      goodVerdict('a2'),
    ]),
    REQ,
  );
  assert.equal(verdicts[0].principle_id, 'R2');
});

test('an empty or over-long justification rejects the whole response', () => {
  assert.throws(
    () =>
      validateResponse(
        response('b1', [{ ...goodVerdict('a1'), justification: '   ' }, goodVerdict('a2')]),
        REQ,
      ),
    /justification is empty/,
  );
  assert.throws(
    () =>
      validateResponse(
        response('b1', [
          { ...goodVerdict('a1'), justification: 'x'.repeat(1001) },
          goodVerdict('a2'),
        ]),
        REQ,
      ),
    /exceeds 1000 characters/,
  );
});

test('a non-object response is rejected', () => {
  assert.throws(() => validateResponse('nope', REQ), LlmResponseError);
  assert.throws(() => validateResponse([], REQ), LlmResponseError);
  assert.throws(() => validateResponse(null, REQ), LlmResponseError);
});

test('only a high-confidence catalogue violation is flagged', () => {
  const base = { alert_id: 'a', justification: 'j' };
  assert.equal(
    stateForVerdict({
      ...base,
      assessment: 'catalog_violation',
      principle_id: 'P1',
      confidence: 'high',
    }),
    'llm_flagged',
  );
  for (const confidence of /** @type {const} */ (['medium', 'low'])) {
    assert.equal(
      stateForVerdict({ ...base, assessment: 'catalog_violation', principle_id: 'P1', confidence }),
      'needs_review',
    );
  }
  assert.equal(
    stateForVerdict({ ...base, assessment: 'other', principle_id: 'OTHER', confidence: 'high' }),
    'needs_review',
    'other never counts toward flagged',
  );
  assert.equal(
    stateForVerdict({
      ...base,
      assessment: 'no_violation',
      principle_id: 'NONE',
      confidence: 'high',
    }),
    'assessed_good',
  );
});

test('the structured-output schema is closed at both levels', () => {
  const schema = /** @type {any} */ (responseJsonSchema());
  assert.equal(schema.additionalProperties, false);
  assert.equal(schema.properties.verdicts.items.additionalProperties, false);
});

// ---------------------------------------------------------------- assessment

/** @param {Partial<Parameters<typeof assessAlerts>[0]>} overrides */
function assessArgs(overrides) {
  return {
    alerts: [],
    client: new FakeLlmClient(),
    systemPrompt: 'system',
    runId: 'run-1',
    promptVersion: '1.0.0',
    modelVersion: 'fake-model-1',
    now: new Date('2026-08-25T18:00:00Z'),
    ...overrides,
  };
}

test('every eligible alert receives exactly one outcome', async () => {
  const alerts = [v1Row({ key_field: 'a' }), v1Row({ key_field: 'b' })];
  const result = await assessAlerts(assessArgs({ alerts }));
  assert.equal(result.outcomes.size, 2);
  for (const outcome of result.outcomes.values()) assert.equal(outcome.state, 'assessed_good');
});

test('a durable verdict is reused instead of being requested again', async () => {
  const alert = v1Row({ key_field: 'a' });
  const client = new FakeLlmClient();
  const existing = new Map([
    [
      verdictKey(alert.application, alert.keyField),
      {
        assessment: 'catalog_violation',
        principle_id: 'P2',
        confidence: 'high',
        justification: 'stored earlier',
      },
    ],
  ]);
  const result = await assessAlerts(
    assessArgs({ alerts: [alert], client, existingVerdicts: existing }),
  );

  assert.equal(client.calls.length, 0, 'no request should be made for a stored verdict');
  assert.equal(result.reusedVerdicts, 1);
  assert.equal(result.newVerdicts.length, 0);
  const outcome = result.outcomes.get(alert.identity);
  assert.equal(outcome.state, 'llm_flagged');
  assert.equal(outcome.reused, true);
});

test('a successful batch produces durable verdict rows carrying the judged document', async () => {
  const alert = v1Row({ key_field: 'a' });
  const result = await assessAlerts(assessArgs({ alerts: [alert] }));
  assert.equal(result.newVerdicts.length, 1);
  const verdict = result.newVerdicts[0];
  assert.equal(verdict.application, alert.application);
  assert.equal(verdict.doc_hash, alert.docHash);
  assert.deepEqual(JSON.parse(verdict.representative_doc), alert.source);
  assert.equal(verdict.prompt_version, '1.0.0');
  assert.equal(verdict.model_version, 'fake-model-1');
});

test('a failed attempt retries the identical batch byte-for-byte', async () => {
  const alerts = [v1Row({ key_field: 'a' }), v1Row({ key_field: 'b' })];
  const client = new FakeLlmClient();
  const batchId = buildBatches(alerts, RUN)[0].batchId;
  client.on(batchId, 1, { kind: 'transport_error' });
  client.on(batchId, 2, { kind: 'invalid_json' });

  const result = await assessAlerts(assessArgs({ alerts, client }));

  const payloads = client.payloadsFor(batchId);
  assert.equal(payloads.length, 3);
  assert.equal(new Set(payloads).size, 1, 'every attempt must send identical bytes');
  assert.equal(result.batchAttempts.length, 3);
  assert.deepEqual(
    result.batchAttempts.map((a) => a.status),
    ['transport_error', 'invalid_response', 'succeeded'],
  );
  assert.equal(new Set(result.batchAttempts.map((a) => a.request_hash)).size, 1);
});

test('a batch stops after exactly three attempts and never a fourth', async () => {
  const alerts = [v1Row({ key_field: 'a' })];
  const client = new FakeLlmClient({ fallback: { kind: 'transport_error' } });
  const result = await assessAlerts(assessArgs({ alerts, client }));

  assert.equal(client.calls.length, MAX_ATTEMPTS);
  assert.equal(MAX_ATTEMPTS, 3);
  assert.equal(result.batchAttempts.length, 3);
});

test('an exhausted batch marks every member unassessed with the shared reason', async () => {
  const alerts = [v1Row({ key_field: 'a' }), v1Row({ key_field: 'b' }), v1Row({ key_field: 'c' })];
  const client = new FakeLlmClient({ fallback: { kind: 'timeout' } });
  const result = await assessAlerts(assessArgs({ alerts, client }));

  assert.equal(result.outcomes.size, 3);
  const reasons = new Set();
  for (const outcome of result.outcomes.values()) {
    assert.equal(outcome.state, 'unassessed');
    assert.ok(outcome.unassessedReason);
    reasons.add(outcome.unassessedReason);
  }
  assert.equal(reasons.size, 1, 'the whole batch shares one failure reason');
  assert.equal(result.newVerdicts.length, 0, 'a failed batch stores no verdicts');
});

test('an invalid response is never partially accepted', async () => {
  const alerts = [v1Row({ key_field: 'a' }), v1Row({ key_field: 'b' })];
  const batchId = buildBatches(alerts, RUN)[0].batchId;
  const client = new FakeLlmClient({
    // A response that answers only one of the two alerts.
    fallback: {
      kind: 'raw',
      text: JSON.stringify({ batch_id: batchId, verdicts: [goodVerdict('whatever')] }),
    },
  });
  const result = await assessAlerts(assessArgs({ alerts, client }));

  for (const outcome of result.outcomes.values()) assert.equal(outcome.state, 'unassessed');
  assert.equal(result.newVerdicts.length, 0);
});

test('a batch that recovers on the third attempt is assessed normally', async () => {
  const alerts = [v1Row({ key_field: 'a' })];
  const client = new FakeLlmClient();
  const batchId = buildBatches(alerts, RUN)[0].batchId;
  client.on(batchId, 1, { kind: 'timeout' });
  client.on(batchId, 2, { kind: 'transport_error' });

  const result = await assessAlerts(assessArgs({ alerts, client }));
  assert.equal([...result.outcomes.values()][0].state, 'assessed_good');
  assert.equal(result.newVerdicts.length, 1);
});

test('one failing batch does not affect another batch in the same run', async () => {
  const alerts = [
    v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1' }),
    v1Row({ key_field: 'b', alert_rule_url: 'https://g/d/2' }),
  ];
  const batches = buildBatches(alerts, RUN);
  const client = new FakeLlmClient();
  for (let attempt = 1; attempt <= 3; attempt++) {
    client.on(batches[0].batchId, attempt, { kind: 'transport_error' });
  }

  const result = await assessAlerts(assessArgs({ alerts, client }));
  const states = [...result.outcomes.values()].map((o) => o.state).sort();
  assert.deepEqual(states, ['assessed_good', 'unassessed']);
});

test('verdict states map through from the model response', async () => {
  const alerts = [
    v1Row({ key_field: 'a', alert_rule_url: 'https://g/d/1' }),
    v1Row({ key_field: 'b', alert_rule_url: 'https://g/d/1' }),
    v1Row({ key_field: 'c', alert_rule_url: 'https://g/d/1' }),
  ];
  const batchId = buildBatches(alerts, RUN)[0].batchId;
  const client = new FakeLlmClient();
  client.on(
    batchId,
    1,
    scriptedVerdicts(
      (_alert, i) =>
        [
          {
            assessment: 'catalog_violation',
            principle_id: 'P1',
            confidence: 'high',
            justification: 'j',
          },
          {
            assessment: 'catalog_violation',
            principle_id: 'P1',
            confidence: 'low',
            justification: 'j',
          },
          {
            assessment: 'no_violation',
            principle_id: 'NONE',
            confidence: 'high',
            justification: 'j',
          },
        ][i],
    ),
  );

  const result = await assessAlerts(assessArgs({ alerts, client }));
  const byKey = new Map([...result.outcomes.values()].map((o) => [o.alert.keyField, o.state]));
  assert.equal(byKey.get('a'), 'llm_flagged');
  assert.equal(byKey.get('b'), 'needs_review');
  assert.equal(byKey.get('c'), 'assessed_good');
});

test('running with the model switched off marks identities unassessed with a reason', () => {
  const outcomes = markAllUnassessed([v1Row({ key_field: 'a' })], 'llm disabled for this run');
  const outcome = [...outcomes.values()][0];
  assert.equal(outcome.state, 'unassessed');
  assert.equal(outcome.unassessedReason, 'llm disabled for this run');
});

// -------------------------------------------------------------------- prompt

test('the prompt carries both guides verbatim and the full catalogue', () => {
  const { systemPrompt, promptVersion, systemPromptHash } = buildPrompt();
  assert.match(systemPrompt, /BEGIN Alerting_Guide_Appchi_EN\.md/);
  assert.match(systemPrompt, /BEGIN what_is_an_incorrect_alert_EN\.md/);
  assert.match(systemPrompt, /The Wake-Up Test/);
  assert.match(systemPrompt, /that's a log/i);
  for (const id of ['P1', 'P7', 'P11', 'R1', 'R10']) {
    assert.match(systemPrompt, new RegExp(`\\b${id}\\b`), id);
  }
  assert.equal(promptVersion, '1.0.0');
  assert.equal(systemPromptHash.length, 64);
});

test('the prompt instructs independent per-alert assessment', () => {
  const { systemPrompt } = buildPrompt();
  assert.match(systemPrompt, /Assess every alert INDEPENDENTLY/);
  assert.match(systemPrompt, /Never copy a\nneighbour's verdict/);
  assert.match(systemPrompt, /Default to "no_violation"/);
});
