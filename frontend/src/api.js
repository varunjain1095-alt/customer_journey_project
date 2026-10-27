// API helpers. Each caller passes an AbortSignal; callers create a new
// AbortController per request group so stale responses are dropped (edge 6.2).

const qs = (params) => {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, x))
    else if (v !== undefined && v !== null) sp.set(k, v)
  }
  return sp.toString()
}

const get = (path, params, signal) =>
  fetch(`/api/${path}?${qs(params)}`, { signal }).then((r) => r.json())

export const fetchVariants = (datasetVersion, topN, signal) =>
  get('variants', { dataset_version: datasetVersion, case_id: 'user_session', top_n: topN }, signal)

export const fetchDfg = (datasetVersion, granularity, coverage, signal) =>
  get('dfg', { dataset_version: datasetVersion, case_id: 'user_session', coverage, granularity }, signal)

export const fetchFunnel = (datasetVersion, path, signal) =>
  get('funnel', { dataset_version: datasetVersion, case_id: 'user_session', path }, signal)

export const fetchSegment = (datasetVersion, path, signal, edge) =>
  get(
    'segment',
    {
      dataset_version: datasetVersion,
      case_id: 'user_session',
      path,
      edge_source: edge?.source,
      edge_target: edge?.target,
    },
    signal
  )

export const fetchHypotheses = (datasetVersion = 'PRE', signal) =>
  get('hypotheses', { dataset_version: datasetVersion }, signal)

export const saveHypothesis = (payload) =>
  fetch('/api/hypotheses', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then((r) => r.json())

export const deleteHypothesis = (edgeKey, datasetVersion = 'PRE') =>
  fetch('/api/hypotheses', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ edge_key: edgeKey, dataset_version: datasetVersion }),
  }).then((r) => r.json())

export const fetchStarred = (signal) => get('starred', {}, signal)

export const setStar = (pathId, datasetVersion, starred) =>
  fetch('/api/star', {
    method: starred ? 'POST' : 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path_id: pathId, dataset_version: datasetVersion }),
  }).then((r) => r.json())

export const requestAnimation = (datasetVersion) =>
  fetch('/api/animate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_version: datasetVersion }),
  }).then((r) => r.json())

// Pre-rendered whole-process animation shipped with the static build (see
// scripts/precompute_animations.py). One file per dataset_version.
export const fetchStaticAnimation = (datasetVersion) =>
  fetch(`animations/${datasetVersion}.html`)
    .then((r) => (r.ok ? r.text() : null))
    .then((html) => (html && html.includes('htmlwidget') ? html : null))
    .catch(() => null)
