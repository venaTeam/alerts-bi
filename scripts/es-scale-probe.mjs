// Measures the numbers that size the alerts BI: how many DISTINCT alerts exist, how fast
// their keys churn, and how much of the distinct count is node_name inflation.
//
// Read-only — issues aggregations only, never writes. Safe to point at production ECK.
//
//   ES_URL=https://eck.internal:9200 ES_AUTH=user:pass node scripts/es-scale-probe.mjs
//   ES_URL=... DAYS=30 node scripts/es-scale-probe.mjs
//
// "Distinct alert" throughout means a distinct **application + key_field** pair, which is
// the primary key of an alert (design doc section 1.1). key_field ALONE is not sufficient:
// its application+object+node_name form is only a default, and a sender may set its own,
// so the same key_field value can occur under two applications.
//
// Every count is built from terms + cardinality on indexed fields — no painless scripts,
// so this still runs on clusters with inline scripting disabled.
//
// Answers, per schema:
//   1. distinct alerts over the window, and per day
//   2. key reuse ratio (sum of daily distincts / window distinct)  <- sizes the LLM cache
//   3. node_name inflation: distinct alerts vs. distinct application+object/component
//   4. which applications mint the most node_name values

const ES = process.env.ES_URL || 'http://localhost:9200';
const DAYS = Number(process.env.DAYS || 30);
const AUTH = process.env.ES_AUTH;

// cardinality is HyperLogLog++ and approximate above its precision threshold.
// 40000 is the maximum ES accepts. Summing per-application sketches keeps each one
// well under that in most cases, but treat totals as sizing figures, not metrics.
const PRECISION = 40000;

async function es(index, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (AUTH) headers.Authorization = `Basic ${Buffer.from(AUTH).toString('base64')}`;
  const res = await fetch(`${ES}/${index}/_search`, { method: 'POST', headers, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(`${index}: ${res.status} ${await res.text()}`);
  return res.json();
}

const range = { range: { '@timestamp': { gte: `now-${DAYS}d`, lte: 'now' } } };

// Distinct (application, X) pairs, without scripting: bucket by application, count
// distinct X inside each bucket, sum the buckets. Exact on the pairing, approximate
// only within each application's sketch.
function pairAgg(field, appBuckets) {
  return {
    by_app: {
      terms: { field: 'application', size: appBuckets },
      aggs: { d: { cardinality: { field, precision_threshold: PRECISION } } },
    },
  };
}
const sumPairs = (agg) => agg.buckets.reduce((a, b) => a + b.d.value, 0);
const truncated = (agg) => (agg.sum_other_doc_count ?? 0) > 0;

async function probe(index, componentField) {
  console.log(`\n${'='.repeat(72)}\n${index}  (last ${DAYS} days)\n${'='.repeat(72)}`);

  // how many applications are there? sizes every terms agg below
  const appCount = await es(index, {
    size: 0, track_total_hits: true, query: range,
    aggs: { apps: { cardinality: { field: 'application', precision_threshold: PRECISION } } },
  });
  const rows = appCount.hits.total.value;
  const apps = appCount.aggregations.apps.value;
  const appBuckets = Math.max(apps * 2, 100); // headroom so no application is dropped

  const totals = await es(index, {
    size: 0, query: range,
    aggs: {
      keys: pairAgg('key_field', appBuckets).by_app,
      defs: pairAgg(componentField, appBuckets).by_app,
      nodes: pairAgg('node_name', appBuckets).by_app,
    },
  });
  const keys = sumPairs(totals.aggregations.keys);
  const defs = sumPairs(totals.aggregations.defs);
  const nodes = sumPairs(totals.aggregations.nodes);

  if (truncated(totals.aggregations.keys)) {
    console.log(`  !! terms agg truncated — more than ${appBuckets} applications. Counts are LOW.`);
  }

  console.log(`  rows in window                     : ${rows.toLocaleString()}`);
  console.log(`  distinct applications              : ${apps.toLocaleString()}`);
  console.log(`  DISTINCT ALERTS (application+key)  : ${keys.toLocaleString()}`);
  console.log(`  distinct application+${componentField.padEnd(10)}    : ${defs.toLocaleString()}   <- alert definitions, node-independent`);
  console.log(`  distinct application+node_name     : ${nodes.toLocaleString()}`);
  console.log(`  rows per distinct alert            : ${(rows / Math.max(keys, 1)).toFixed(1)}`);
  console.log(`  NODE INFLATION FACTOR              : ${(keys / Math.max(defs, 1)).toFixed(1)}x`);
  if (keys / Math.max(defs, 1) > 3) {
    console.log(`     ^ high — much of the distinct count is node_name churn, not alert inventory.`);
  }

  // --- distinct per day, to measure key churn ---
  const daily = await es(index, {
    size: 0, query: range,
    aggs: {
      per_day: {
        date_histogram: { field: '@timestamp', calendar_interval: 'day', min_doc_count: 1 },
        aggs: pairAgg('key_field', appBuckets),
      },
    },
  });
  const buckets = daily.aggregations.per_day.buckets;
  if (buckets.length) {
    const perDay = buckets.map((b) => sumPairs(b.by_app));
    const sumDays = perDay.reduce((a, b) => a + b, 0);
    const avgDay = sumDays / perDay.length;
    console.log(`\n  distinct alerts per day            : avg ${Math.round(avgDay).toLocaleString()}  (min ${Math.min(...perDay).toLocaleString()}, max ${Math.max(...perDay).toLocaleString()})`);
    console.log(`  sum of daily distincts             : ${sumDays.toLocaleString()}`);
    console.log(`  distinct over whole window         : ${keys.toLocaleString()}`);
    console.log(`  KEY REUSE RATIO                    : ${(sumDays / Math.max(keys, 1)).toFixed(1)}x  (over ${buckets.length} days with data)`);
    console.log(`     ~${buckets.length}x -> the same keys recur daily; the verdict cache hits and classification is cheap.`);
    console.log(`     ~1x  -> keys are nearly all new each day; the cache never hits and every run pays full price.`);
  }

  // --- where node_name is minting the most keys ---
  const off = totals.aggregations.nodes.buckets.filter((b) => b.d.value > 1)
    .sort((a, b) => b.d.value - a.d.value);
  if (off.length) {
    console.log(`\n  applications with the most distinct node_name values:`);
    for (const b of off.slice(0, 10)) {
      console.log(`     ${String(b.key).padEnd(28)} ${String(b.d.value).padStart(7)} nodes  (${b.doc_count.toLocaleString()} rows)`);
    }
    console.log(`     ^ hash- or random-suffixed pod names here confirm the inflation (design doc 7.3.1).`);
  }
}

await probe('appchi-v1', 'object');
await probe('appchi-v2', 'component');

console.log(`\nNote: cardinality is approximate above ${PRECISION.toLocaleString()} per bucket. Good enough for sizing;`);
console.log(`for exact reported metrics use a composite aggregation over [application, key_field].`);
