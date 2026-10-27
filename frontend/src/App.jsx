import { useCallback, useEffect, useRef, useState } from 'react'
import ProcessDiagram from './components/ProcessDiagram.jsx'
import VariantTable from './components/VariantTable.jsx'
import { FunnelChart } from './components/FunnelPanel.jsx'
import SegmentPanel, { EdgeSegmentDrilldown } from './components/SegmentPanel.jsx'
import OpportunitiesPanel from './components/OpportunitiesPanel.jsx'
import * as api from './api.js'

const MAX_COMPARE = 4

export default function App() {
  const datasetVersion = 'PRE'
  const granularity = 'full'
  const coverage = 1.0
  const measure = 'frequency'
  const topN = 10
  const [variants, setVariants] = useState(null)
  const [dfg, setDfg] = useState(null)
  const [selected, setSelected] = useState(null)          // variant row object
  const [selectedEdge, setSelectedEdge] = useState(null)  // {source, target} leak edge
  const [edgeBreakdown, setEdgeBreakdown] = useState(null)
  const [hypotheses, setHypotheses] = useState([])
  const [compare, setCompare] = useState([])              // variant row objects (2-4)
  const [stars, setStars] = useState({})                  // path_id -> dataset_version
  const [funnels, setFunnels] = useState([])              // [{label, version, funnel}]
  const [segments, setSegments] = useState([])            // [{label, version, profile}]
  const [inlineAnim, setInlineAnim] = useState(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState(null)
  const abortRef = useRef(null)

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(null), 3500) }

  // Load variants + DFG whenever version/granularity/topN changes.
  // Stale fetches are aborted so out-of-order responses can't win (edge 6.2).
  useEffect(() => {
    abortRef.current?.abort()
    const ctl = new AbortController()
    abortRef.current = ctl
    const version = datasetVersion === 'COMPARISON' ? 'PRE' : datasetVersion

    setVariants(null); setDfg(null)
    Promise.all([
      api.fetchVariants(version, topN, ctl.signal),
      api.fetchDfg(version, granularity, coverage, ctl.signal),
    ]).then(([v, d]) => {
      if (ctl.signal.aborted) return
      setVariants(v); setDfg(d)
      setSelected((cur) => (cur && v.top.some((r) => r.path_id === cur.path_id) ? cur : v.top[2] || v.top[0] || null))
      // Default to top-1 leak edge so Feature 2 WHO drill-down is live on open
      if (d?.edges?.length) {
        const topLeak = [...d.edges]
          .filter((e) => e.source !== 'purchase' && (e.lost_volume ?? 0) > 0)
          .sort((a, b) => b.lost_volume - a.lost_volume)[0]
        if (topLeak) setSelectedEdge((cur) => cur || { source: topLeak.source, target: topLeak.target })
      }
      setCompare([])
    }).catch(() => {})
    return () => ctl.abort()
  }, [datasetVersion, granularity, topN, coverage])

  const reloadHypotheses = useCallback(() => {
    api.fetchHypotheses('PRE').then((r) => setHypotheses(r.hypotheses || [])).catch(() => {})
  }, [])

  useEffect(() => {
    api.fetchStarred().then((r) => {
      const m = {}
      r.starred?.forEach((s) => (m[s.path_id] = s.dataset_version))
      setStars(m)
    }).catch(() => {})
    reloadHypotheses()
  }, [reloadHypotheses])

  // Feature 2: Fetch WHO segmentation breakdown via /api/segment whenever selectedEdge changes
  useEffect(() => {
    if (!selectedEdge) {
      setEdgeBreakdown(null)
      return
    }
    const ctl = new AbortController()
    api.fetchSegment('PRE', [], ctl.signal, selectedEdge)
      .then((res) => { if (!ctl.signal.aborted) setEdgeBreakdown(res) })
      .catch(() => {})
    return () => ctl.abort()
  }, [selectedEdge])

  // Funnels + segments for the displayed paths (compare set or selected),
  // dual-fetched in COMPARISON mode.
  useEffect(() => {
    const ctl = new AbortController()
    const displayPaths = compare.length >= 2 ? compare : selected ? [selected] : []
    const versions = datasetVersion === 'COMPARISON' ? ['PRE', 'POST'] : [datasetVersion]
    const jobs = []
    displayPaths.forEach((v) => versions.forEach((ver) => {
      jobs.push(['f', v, ver, api.fetchFunnel(ver, v.path, ctl.signal)])
      jobs.push(['s', v, ver, api.fetchSegment(ver, v.path, ctl.signal)])
    }))
    setBusy(displayPaths.length > 0)
    Promise.all(jobs.map(([, , , p]) => p)).then((results) => {
      if (ctl.signal.aborted) return
      const f = [], s = []
      results.forEach((res, i) => {
        const [kind, v, ver] = jobs[i]
        const pathLabel = v.path.join(' → ')
        const short = pathLabel.length > 70 ? pathLabel.slice(0, 70) + '…' : pathLabel
        const label = `Rank ${v.rank}: ${short}`
        if (kind === 'f') f.push({ label, version: ver, funnel: res, path: v.path })
        else s.push({ label, version: ver, profile: res, path: v.path })
      })
      setFunnels(f); setSegments(s); setBusy(false)
    }).catch(() => { if (!ctl.signal.aborted) setBusy(false) })
    return () => ctl.abort()
  }, [selected, compare, datasetVersion])

  // Auto-load the combined replay for the active dataset version.
  useEffect(() => {
    const ver = datasetVersion === 'COMPARISON' ? 'PRE' : datasetVersion
    let cancelled = false
    setInlineAnim({ status: 'loading' })
    api.fetchStaticAnimation(ver)
      .then((html) => { if (!cancelled) setInlineAnim(html ? { status: 'ok', html } : { status: 'empty' }) })
      .catch(() => { if (!cancelled) setInlineAnim({ status: 'empty' }) })
    return () => { cancelled = true }
  }, [datasetVersion])

  const toggleCompare = useCallback((v) => {
    setCompare((cur) => {
      if (cur.some((c) => c.path_id === v.path_id)) return cur.filter((c) => c.path_id !== v.path_id)
      if (cur.length >= MAX_COMPARE) {
        showToast('Maximum 4 paths can be compared side-by-side')
        return cur
      }
      return [...cur, v]
    })
  }, [])

  const toggleStar = useCallback((v) => {
    const isStarred = stars[v.path_id] === datasetVersion
    api.setStar(v.path_id, datasetVersion, !isStarred).then(() =>
      setStars((cur) => {
        const m = { ...cur }
        if (isStarred) delete m[v.path_id]; else m[v.path_id] = datasetVersion
        return m
      }))
  }, [stars, datasetVersion])

  const version = datasetVersion === 'COMPARISON' ? 'PRE' : datasetVersion
  const compareIds = new Set(compare.map((c) => c.path_id))
  const gridCols = Math.max(funnels.length, 1)

  // Per-step counts for the selected path: used to label diagram edges the
  // 60% coverage filter pruned.
  const selectedFunnel = funnels.find(
    (f) => f.version === version && selected && f.path.join('|') === selected.path.join('|'))
  const pathStepCounts = selectedFunnel?.funnel?.steps?.map((s) => s.count)

  const handleSaveHypothesis = useCallback((payload) => {
    api.saveHypothesis(payload).then(() => {
      reloadHypotheses()
      showToast(`Saved hypothesis for ${payload.edge_key}`)
    })
  }, [reloadHypotheses])

  const handleDeleteHypothesis = useCallback((edgeKey) => {
    api.deleteHypothesis(edgeKey, 'PRE').then(() => {
      reloadHypotheses()
      showToast(`Removed hypothesis for ${edgeKey}`)
    })
  }, [reloadHypotheses])

  return (
    <>
      <header>
        <h1>Customer Journey Analytics: PM Decision Layer</h1>
        <div className="controls">
          <div className="toggle">
            <button className="active">Dashboard</button>
          </div>
        </div>
      </header>
      <main>
        <div className="panel">
          <h2>Process Diagram</h2>
          <div className="diagram-legend">
            <span className="lg"><span className="sw" style={{ background: '#dc2626' }} /> Highlighted leak edge</span>
            <span className="lg"><span className="sw" style={{ background: '#ff7f0e' }} /> Selected path</span>
            <span className="lg"><span className="sw" style={{ background: '#94a3b8' }} /> Other steps</span>
          </div>
          <div className="hero-grid">
            <div className="hero-replay">
              <div className="replay-head">Animated Replay: orange = purchased, grey = dropped off</div>
              {inlineAnim?.status === 'ok'
                ? <iframe title="inline-replay" srcDoc={inlineAnim.html} className="replay-frame" />
                : <p className="loading">{inlineAnim?.status === 'loading'
                    ? 'Loading replay...'
                    : 'Replay unavailable for this version.'}</p>}
            </div>
            <div className="hero-map">
              {dfg
                ? <ProcessDiagram dfg={dfg} selectedPath={selected?.path} pathStepCounts={pathStepCounts} selectedEdge={selectedEdge} measure={measure} />
                : <p className="loading">Loading process model: first load mines 1.7M events...</p>}
            </div>
          </div>
        </div>

        <OpportunitiesPanel
          dfg={dfg}
          selectedEdge={selectedEdge}
          onSelectEdge={setSelectedEdge}
          hypotheses={hypotheses}
          onSaveHypothesis={handleSaveHypothesis}
          onDeleteHypothesis={handleDeleteHypothesis}
        />

        <EdgeSegmentDrilldown edgeBreakdown={edgeBreakdown} />

        <div className="panel">
          <h2>Path Variants</h2>
          {!variants && <p className="loading">Loading variants...</p>}
          {variants && (
            <VariantTable
              variants={variants}
              selectedPathId={selected?.path_id}
              onSelect={setSelected}
              compareIds={compareIds}
              onToggleCompare={toggleCompare}
              stars={stars}
              onToggleStar={toggleStar}
              datasetVersion={version}
            />
          )}
        </div>

        <div className="panel">
          <h2>Funnels{compare.length >= 2 ? `: comparing ${compare.length} paths` : ''}</h2>
          {busy && <p className="loading">Profiling cohort...</p>}
          <div className="compare-grid" style={{ gridTemplateColumns: `repeat(${gridCols}, 1fr)` }}>
            {funnels.map((f, i) => (
              <div key={i}>
                <h3 style={{ fontSize: 12, margin: '4px 0' }}>{f.label}</h3>
                <FunnelChart funnel={f.funnel} />
              </div>
            ))}
          </div>
        </div>

        {segments.map((s, i) => (
          <SegmentPanel key={i} profile={s.profile} label={s.label} />
        ))}
      </main>
      {toast && <div className="toast">{toast}</div>}
    </>
  )
}
