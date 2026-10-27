import { useMemo } from 'react'
import { ReactFlow, Background, Controls } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

// Layered layout: longest-path depth from start activities -> column.
function layout(dfg) {
  const preds = {}
  const succs = {}
  dfg.edges.forEach(({ source, target }) => {
    ;(preds[target] ??= []).push(source)
    ;(succs[source] ??= []).push(target)
  })
  const depth = {}
  const queue = dfg.nodes.filter((n) => !preds[n])
  queue.forEach((n) => (depth[n] = 0))
  while (queue.length) {
    const cur = queue.shift()
    for (const nxt of succs[cur] || []) {
      const d = depth[cur] + 1
      if (!(nxt in depth) || d > depth[nxt]) {
        depth[nxt] = d
        queue.push(nxt)
      }
    }
  }
  const colCounts = {}
  return dfg.nodes.map((n) => {
    const col = depth[n] ?? 0
    const row = (colCounts[col] = (colCounts[col] ?? -1) + 1)
    return { id: n, position: { x: col * 280, y: row * 110 }, data: { label: n } }
  })
}

const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n))

const fmtDur = (s) => {
  if (s == null) return undefined
  if (s < 90) return `${s.toFixed(0)}s`
  if (s < 5400) return `${(s / 60).toFixed(1)}m`
  if (s < 172800) return `${(s / 3600).toFixed(1)}h`
  return `${(s / 86400).toFixed(1)}d`
}

export default function ProcessDiagram({ dfg, selectedPath, pathStepCounts, selectedEdge, measure = 'frequency' }) {
  const { nodes, edges } = useMemo(() => {
    if (!dfg?.nodes?.length) return { nodes: [], edges: [] }
    const pathNodes = new Set(selectedPath || [])
    const pathEdges = new Set()
    for (let i = 0; i + 1 < (selectedPath || []).length; i++)
      pathEdges.add(`${selectedPath[i]}→${selectedPath[i + 1]}`)
    const focusEdgeKey = selectedEdge ? `${selectedEdge.source}→${selectedEdge.target}` : null
    const focusNodes = selectedEdge ? new Set([selectedEdge.source, selectedEdge.target]) : new Set()

    // The 60% coverage filter can prune nodes/edges that belong to the selected
    // path: overlay them so the full path always renders. Synthesized edges use
    // the funnel's per-step count (sessions reaching that step of the path).
    const aug = { nodes: [...dfg.nodes], edges: [...dfg.edges] }
    const haveNode = new Set(dfg.nodes)
    const haveEdge = new Set(dfg.edges.map((e) => `${e.source}→${e.target}`))
    ;(selectedPath || []).forEach((n, i) => {
      if (!haveNode.has(n)) { aug.nodes.push(n); haveNode.add(n) }
      if (i > 0) {
        const key = `${selectedPath[i - 1]}→${n}`
        if (!haveEdge.has(key)) {
          aug.edges.push({ source: selectedPath[i - 1], target: n, count: pathStepCounts?.[i] ?? null })
          haveEdge.add(key)
        }
      }
    })

    const nodes = layout(aug).map((n) => {
      const isLeakNode = focusNodes.has(n.id)
      const hot = pathNodes.has(n.id)
      return {
        ...n,
        style: {
          background: isLeakNode ? '#fee2e2' : hot ? '#ffedd5' : '#f1f5f9',
          border: `2px solid ${isLeakNode ? '#dc2626' : hot ? '#ff7f0e' : '#94a3b8'}`,
          boxShadow: isLeakNode ? '0 0 0 3px rgba(220,38,38,0.35)' : undefined,
          borderRadius: 8, padding: '8px 14px',
          fontSize: 13, fontWeight: 600, color: '#0f172a',
          opacity: isLeakNode || hot || !selectedPath ? 1 : 0.35,
        },
      }
    })
    const perfVals = aug.edges.map((e) => e.perf_median).filter((v) => v != null)
    const perfMax = perfVals.length ? Math.max(...perfVals) : 1
    const edges = aug.edges.map((e, i) => {
      const eKey = `${e.source}→${e.target}`
      const isLeakEdge = focusEdgeKey === eKey
      const hot = pathEdges.has(eKey)
      // Labels share the edge's emphasis: a dimmed edge keeps no floating number.
      const dim = selectedPath && !hot && !isLeakEdge
      const perf = e.perf_median
      const isPerf = measure === 'performance'
      const label = dim
        ? undefined
        : isPerf ? fmtDur(perf) : (e.count == null ? undefined : fmt(e.count))
      // slowness ramp (slate → red) for performance; the existing grey for frequency
      const t = isPerf && perf != null ? Math.min(perf / perfMax, 1) : 0
      const perfColor = `rgb(${Math.round(203 + t * (220 - 203))}, ${Math.round(213 - t * 175)}, ${Math.round(225 - t * 187)})`
      const baseStroke = isPerf ? perfColor : '#cbd5e1'
      const strokeColor = isLeakEdge ? '#dc2626' : hot ? '#ff7f0e' : baseStroke
      return {
        id: `e${i}`, source: e.source, target: e.target,
        label,
        title: isPerf
          ? (perf == null ? undefined : `${e.source} → ${e.target}: median ${fmtDur(perf)}`)
          : (e.count == null ? undefined : `${e.source} → ${e.target}: ${e.count.toLocaleString()} transitions`),
        animated: isLeakEdge || hot, type: 'smoothstep',
        markerEnd: { type: 'arrowclosed', color: strokeColor },
        style: { stroke: strokeColor,
                 strokeWidth: isLeakEdge ? 4 : hot ? 3 : (isPerf ? 2 : 1.5), opacity: dim ? 0.35 : 1 },
        labelStyle: { fontSize: 11, fill: '#e2e8f0', fontWeight: 600 },
        labelBgStyle: { fill: isLeakEdge ? '#991b1b' : '#1e293b', fillOpacity: 1 },
        labelBgPadding: [4, 2],
        labelBgBorderRadius: 3,
      }
    })
    return { nodes, edges }
  }, [dfg, selectedPath, selectedEdge, measure])

  return (
    <div id="diagram-wrap">
      <ReactFlow nodes={nodes} edges={edges} fitView fitViewOptions={{ padding: 0.25 }}
        minZoom={0.2} nodesDraggable={false} proOptions={{ hideAttribution: true }}>
        <Background color="#334155" gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
