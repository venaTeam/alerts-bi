// Creates the "Alerts BI — scale probe" Kibana dashboard: how many DISTINCT alerts exist
// (application + key_field), how that splits by team, and how it moves day to day.
//
//   KIBANA_URL=https://kibana.internal:5601 KIBANA_AUTH=user:pass node scripts/create-kibana-panels.mjs
//
// Kibana has no built-in unique-count across two fields, so this first adds an `alert_uid`
// RUNTIME FIELD to each data view that emits `application|key_field` — the primary key of an
// alert (design doc 1.1). key_field alone is NOT sufficient: its application+object+node_name
// form is only a default and a sender may override it.
//
// Runtime fields are computed at query time and need inline painless enabled. At production
// volume that is slow over long windows; if it strains the cluster, mint the same concatenation
// as a real mapped keyword in an ingest pipeline and point the panels at that field instead.
//
// Safe to re-run — every object is written with overwrite=true under a fixed id.

const KB = process.env.KIBANA_URL || 'http://localhost:5601';
const AUTH = process.env.KIBANA_AUTH;
const H = { 'kbn-xsrf': 'true', 'Content-Type': 'application/json' };
if (AUTH) H.Authorization = `Basic ${Buffer.from(AUTH).toString('base64')}`;

// resolve data views by index title rather than hardcoding generated ids
async function dataViews() {
  const res = await fetch(`${KB}/api/data_views`, { headers: H });
  if (!res.ok) throw new Error(`data_views: ${res.status} ${await res.text()}`);
  const { data_view: views } = await res.json();
  const find = (title) => {
    const v = views.find((d) => d.title === title);
    if (!v) throw new Error(`no data view for index "${title}" — create it in Kibana first`);
    return v.id;
  };
  return { v1: find('appchi-v1'), v2: find('appchi-v2') };
}

const DV = await dataViews();

// the runtime field the panels aggregate on
for (const [schema, id] of Object.entries(DV)) {
  const res = await fetch(`${KB}/api/data_views/data_view/${id}/runtime_field`, {
    method: 'POST', headers: H,
    body: JSON.stringify({
      name: 'alert_uid',
      runtimeField: { type: 'keyword', script: { source: 'emit(doc["application"].value + "|" + doc["key_field"].value)' } },
    }),
  });
  const j = await res.json();
  console.log(j.error && !/already exists/i.test(j.message || '')
    ? `ERROR ${schema} runtime field: ${j.message}`
    : `ok  runtime field alert_uid on ${schema}`);
}

async function put(type, id, body) {
  const res = await fetch(`${KB}/api/saved_objects/${type}/${id}?overwrite=true`, {
    method: 'POST', headers: H, body: JSON.stringify(body),
  });
  const j = await res.json();
  console.log(j.error ? `ERROR ${id}: ${j.message}` : `ok  ${type}/${j.id}  — ${j.attributes.title}`);
  return j;
}

const uniq = (label) => ({
  label, dataType: 'number', operationType: 'unique_count',
  sourceField: 'alert_uid', isBucketed: false, scale: 'ratio', params: { emptyAsNull: false },
});
const count = (label) => ({
  label, dataType: 'number', operationType: 'count',
  sourceField: '___records___', isBucketed: false, scale: 'ratio', params: { emptyAsNull: false },
});
const ref = (dv) => [{ type: 'index-pattern', id: dv, name: 'indexpattern-datasource-layer-layer1' }];

// ---- 1/2. big-number metric per schema ----
for (const [schema, dv] of Object.entries(DV)) {
  await put('lens', `alerts-bi-distinct-${schema}`, {
    attributes: {
      title: `Distinct alerts — ${schema} (application + key_field)`,
      description: 'Unique count of application|key_field, the primary key of an alert (design doc 1.1).',
      visualizationType: 'lnsMetric',
      state: {
        datasourceStates: { formBased: { layers: { layer1: {
          columns: { col1: uniq('Distinct alerts'), col2: count('Rows') },
          columnOrder: ['col1', 'col2'], incompleteColumns: {},
        } } } },
        filters: [], query: { language: 'kuery', query: '' },
        visualization: { layerId: 'layer1', layerType: 'data', metricAccessor: 'col1', secondaryMetricAccessor: 'col2' },
      },
    },
    references: ref(dv),
  });
}

// ---- 3. distinct alerts per day (the churn signal) ----
await put('lens', 'alerts-bi-distinct-per-day-v1', {
  attributes: {
    title: 'Distinct alerts per day — v1',
    description: 'Compare the daily figure against the total over the window: if the total is close to a single day, keys recur and the verdict cache hits. If it is close to the sum of days, keys churn.',
    visualizationType: 'lnsXY',
    state: {
      datasourceStates: { formBased: { layers: { layer1: {
        columns: {
          colX: {
            label: '@timestamp', dataType: 'date', operationType: 'date_histogram',
            sourceField: '@timestamp', isBucketed: true, scale: 'interval',
            params: { interval: '1d', includeEmptyRows: true, dropPartials: false },
          },
          col1: uniq('Distinct alerts'),
        },
        columnOrder: ['colX', 'col1'], incompleteColumns: {},
      } } } },
      filters: [], query: { language: 'kuery', query: '' },
      visualization: {
        legend: { isVisible: true, position: 'right' }, valueLabels: 'hide',
        preferredSeriesType: 'bar_stacked', layers: [
          { layerId: 'layer1', layerType: 'data', seriesType: 'bar_stacked', xAccessor: 'colX', accessors: ['col1'] },
        ],
      },
    },
  },
  references: ref(DV.v1),
});

// ---- 4. distinct alerts by operator, with row count alongside ----
await put('lens', 'alerts-bi-distinct-by-operator-v1', {
  attributes: {
    title: 'Distinct alerts by operator — v1',
    description: 'Rows next to distinct alerts: a large gap means one thing stuck, a small gap means many different things firing (design doc 3.3).',
    visualizationType: 'lnsDatatable',
    state: {
      datasourceStates: { formBased: { layers: { layer1: {
        columns: {
          colB: {
            label: 'Operator', dataType: 'string', operationType: 'terms',
            sourceField: 'operator', isBucketed: true, scale: 'ordinal',
            params: { size: 50, orderBy: { type: 'column', columnId: 'col1' }, orderDirection: 'desc' },
          },
          col1: uniq('Distinct alerts'),
          col2: count('Rows'),
        },
        columnOrder: ['colB', 'col1', 'col2'], incompleteColumns: {},
      } } } },
      filters: [], query: { language: 'kuery', query: '' },
      visualization: {
        layerId: 'layer1', layerType: 'data',
        columns: [{ columnId: 'colB' }, { columnId: 'col1' }, { columnId: 'col2' }],
      },
    },
  },
  references: ref(DV.v1),
});

// ---- dashboard tying them together ----
const panels = [
  { id: 'alerts-bi-distinct-v1', w: 12, h: 8, x: 0, y: 0 },
  { id: 'alerts-bi-distinct-v2', w: 12, h: 8, x: 12, y: 0 },
  { id: 'alerts-bi-distinct-per-day-v1', w: 24, h: 12, x: 0, y: 8 },
  { id: 'alerts-bi-distinct-by-operator-v1', w: 24, h: 14, x: 0, y: 20 },
];
await put('dashboard', 'alerts-bi-scale', {
  attributes: {
    title: 'Alerts BI — scale probe',
    description: 'How many DISTINCT alerts exist (application + key_field), how that splits by team, and how it moves day to day.',
    timeRestore: true,
    timeFrom: 'now-90d', timeTo: 'now',
    optionsJSON: JSON.stringify({ hidePanelTitles: false, useMargins: true, syncColors: false }),
    kibanaSavedObjectMeta: { searchSourceJSON: JSON.stringify({ query: { language: 'kuery', query: '' }, filter: [] }) },
    panelsJSON: JSON.stringify(panels.map((p, i) => ({
      version: '8.15.0', type: 'lens',
      gridData: { x: p.x, y: p.y, w: p.w, h: p.h, i: String(i + 1) },
      panelIndex: String(i + 1), embeddableConfig: {}, panelRefName: `panel_${i}`,
    }))),
  },
  references: panels.map((p, i) => ({ name: `panel_${i}`, type: 'lens', id: p.id })),
});
