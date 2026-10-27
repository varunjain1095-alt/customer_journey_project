import { useEffect, useState } from 'react'

const fmtNum = (n) => (n == null ? ':' : Number(n).toLocaleString())
const fmtMoney = (n) =>
  n == null
    ? ':'
    : '$' +
      Number(n).toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
      })

export default function OpportunitiesPanel({
  dfg,
  selectedEdge,
  onSelectEdge,
  hypotheses,
  onSaveHypothesis,
  onDeleteHypothesis,
}) {
  const defaultVal = dfg?.value_defaults?.default_value_per_conversion || 120
  const avgPrice = dfg?.value_defaults?.avg_purchase_price || defaultVal
  const avgAov = dfg?.value_defaults?.avg_order_value || defaultVal

  const [valuePerConv, setValuePerConv] = useState(defaultVal)
  const [recoverablePct, setRecoverablePct] = useState(10)
  const [draftEdgeKey, setDraftEdgeKey] = useState(null)
  const [draftText, setDraftText] = useState('')
  const [draftUrl, setDraftUrl] = useState('')

  useEffect(() => {
    if (dfg?.value_defaults?.default_value_per_conversion) {
      setValuePerConv(dfg.value_defaults.default_value_per_conversion)
    }
  }, [dfg?.value_defaults?.default_value_per_conversion])

  if (!dfg?.edges?.length) {
    return (
      <div className="panel">
        <h2>Opportunities: Ranked Leaks</h2>
        <p className="loading">Computing process leaks...</p>
      </div>
    )
  }

  // Feature 1: Single source of truth = /api/dfg edges.
  // source_total = sum of all DFG out-edges from source + EXIT (end_activities[source]).
  // lost_volume = source_total - edge.count.
  // Exclude edges originating from 'purchase' (already converted).
  const rankedLeaks = [...dfg.edges]
    .filter((e) => e.source !== 'purchase' && (e.lost_volume ?? 0) > 0)
    .sort((a, b) => b.lost_volume - a.lost_volume || b.count - a.count)
    .map((e, i) => {
      const lostDollar = e.lost_volume * valuePerConv
      const recovVol = Math.round(e.lost_volume * (recoverablePct / 100))
      const recovDollar = lostDollar * (recoverablePct / 100)
      return {
        ...e,
        rank: i + 1,
        edgeKey: `${e.source}→${e.target}`,
        lostDollar,
        recovVol,
        recovDollar,
      }
    })

  const hypByEdge = {}
  ;(hypotheses || []).forEach((h) => {
    hypByEdge[h.edge_key] = h
  })

  const timeline = dfg?.timeline || {}
  const changes = timeline.changes || []
  const popHealth = timeline.population_health || {}
  const win = timeline.window || {}

  const openHypothesisEditor = (row, ev) => {
    ev.stopPropagation()
    const existing = hypByEdge[row.edgeKey]
    setDraftEdgeKey(row.edgeKey)
    setDraftText(existing?.hypothesis || '')
    setDraftUrl(
      existing?.experiment_url ||
        `https://experiments.internal/exp/${row.edgeKey.replace('→', '-')}`
    )
  }

  const submitHypothesis = (row, ev) => {
    ev.preventDefault()
    ev.stopPropagation()
    if (!draftText.trim()) return
    onSaveHypothesis({
      edge_key: row.edgeKey,
      dataset_version: 'PRE',
      hypothesis: draftText.trim(),
      experiment_url: draftUrl.trim(),
      baseline_lost_volume: row.lost_volume,
      baseline_edge_count: row.count,
      baseline_dropoff_pct: row.dropoff_pct,
    })
    setDraftEdgeKey(null)
  }

  return (
    <>
      {/* Feature 3: Timeline Change Detection */}
      <div className="panel">
        <div className="panel-head-row">
          <div>
            <h2>
              Timeline Change Detection
              {win.start && win.end
                ? `: ${win.start} to ${win.midpoint} (H1) vs ${win.midpoint} to ${win.end} (H2)`
                : ''}
            </h2>
            <div className="subnote">
              Baseline Behavioral Health (BEHAVIORAL_METRICS): Cart Abandon{' '}
              {((popHealth.cart_abandonment_rate || 0) * 100).toFixed(1)}% · Avg Session{' '}
              {popHealth.session_duration_average ?? ':'}s · Recency{' '}
              {popHealth.days_since_last_purchase ?? ':'}d
            </div>
          </div>
        </div>

        <div className="callout-grid">
          {changes.slice(0, 6).map((c) => {
            const isSel =
              selectedEdge &&
              selectedEdge.source === c.source &&
              selectedEdge.target === c.target
            return (
              <div
                key={c.edge_key}
                className={`callout-card ${c.health_tag} ${isSel ? 'selected' : ''}`}
                onClick={() =>
                  onSelectEdge(
                    isSel ? null : { source: c.source, target: c.target }
                  )
                }
              >
                <div className="callout-top">
                  <span className={`tag trend-${c.trend_tag}`}>
                    {c.trend_tag.toUpperCase()}
                  </span>
                  <span className={`tag health-${c.health_tag}`}>
                    {c.health_tag.toUpperCase()}
                  </span>
                </div>
                <div className="callout-title">{c.callout}</div>
                <div className="callout-meta">
                  Drop rate: {c.h1_drop_rate_pct}% → {c.h2_drop_rate_pct}% (
                  {c.drop_rate_pts_change >= 0 ? '+' : ''}
                  {c.drop_rate_pts_change} pts) · Volume: {fmtNum(c.h1_count)} →{' '}
                  {fmtNum(c.h2_count)} ({c.volume_pct_change >= 0 ? '+' : ''}
                  {c.volume_pct_change}%)
                </div>
                <div className="callout-bm">
                  Abandon:{' '}
                  {(c.behavioral_signals.cart_abandonment_rate * 100).toFixed(1)}% ·
                  Session: {c.behavioral_signals.session_duration_average}s ·
                  Days since purchase:{' '}
                  {c.behavioral_signals.days_since_last_purchase}d
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Feature 1 + Feature 4: Opportunities Panel (Ranked Leaks + Close the Loop) */}
      <div className="panel">
        <div className="panel-head-row">
          <div>
            <h2>Opportunities: Ranked Process Leaks (Single Source: DFG)</h2>
            <div className="subnote">
              <code>source_total</code> = sum of all DFG out-edges from source + EXIT ·{' '}
              <code>lost_volume = source_total − edge.count</code> ·{' '}
              Per-transition gross at-stake value (not additive across steps: no column sum)
            </div>
          </div>
          <div className="opp-controls">
            <label>
              Value / conversion ($):
              <input
                type="number"
                min="1"
                step="1"
                value={valuePerConv}
                onChange={(e) => setValuePerConv(Math.max(0, Number(e.target.value) || 0))}
                className="num-input"
              />
            </label>
            <button
              className="btn"
              onClick={() => setValuePerConv(avgPrice)}
              title="TRANSACTION_EVENTS AVG(price) on purchases"
            >
              Use Purchase Price (${avgPrice})
            </button>
            <button
              className="btn"
              onClick={() => setValuePerConv(avgAov)}
              title="BEHAVIORAL_METRICS AVG(average_order_value)"
            >
              Use AOV (${avgAov})
            </button>
            <label>
              Recoverable X%:
              <input
                type="number"
                min="1"
                max="100"
                step="1"
                value={recoverablePct}
                onChange={(e) =>
                  setRecoverablePct(
                    Math.min(100, Math.max(1, Number(e.target.value) || 1))
                  )
                }
                className="num-input"
              />
              %
            </label>
            {selectedEdge && (
              <button className="btn" onClick={() => onSelectEdge(null)}>
                Clear Edge Selection ({selectedEdge.source} → {selectedEdge.target})
              </button>
            )}
          </div>
        </div>

        <table>
          <thead>
            <tr>
              <th>Rank</th>
              <th>Edge: Click to Highlight & Drill Down WHO</th>
              <th>Advancing</th>
              <th>Source Out-Sum</th>
              <th>Lost Volume (Drop %)</th>
              <th>Direct EXIT at Source</th>
              <th>Lost $ (At-Stake)</th>
              <th>Recoverable @ {recoverablePct}%</th>
              <th>Hypothesis & Before / After</th>
            </tr>
          </thead>
          <tbody>
            {rankedLeaks.map((r) => {
              const isSel =
                selectedEdge &&
                selectedEdge.source === r.source &&
                selectedEdge.target === r.target
              const hyp = hypByEdge[r.edgeKey]
              const isEditing = draftEdgeKey === r.edgeKey
              return (
                <tr
                  key={r.edgeKey}
                  className={`variant ${isSel ? 'selected-leak' : ''}`}
                  onClick={() =>
                    onSelectEdge(
                      isSel ? null : { source: r.source, target: r.target }
                    )
                  }
                >
                  <td>#{r.rank}</td>
                  <td className="path">
                    {r.source} → {r.target}
                  </td>
                  <td>{fmtNum(r.count)}</td>
                  <td>{fmtNum(r.source_total)}</td>
                  <td>
                    <span style={{ color: '#dc2626', fontWeight: 600 }}>{fmtNum(r.lost_volume)}</span>{' '}
                    <span style={{ color: '#6b7280', fontSize: 11 }}>
                      ({r.dropoff_pct}%)
                    </span>
                  </td>
                  <td>{fmtNum(r.exit_at_source)}</td>
                  <td>{fmtMoney(r.lostDollar)}</td>
                  <td>
                    <span style={{ color: '#059669', fontWeight: 600 }}>{fmtMoney(r.recovDollar)}</span>{' '}
                    <span style={{ color: '#6b7280', fontSize: 11 }}>
                      ({fmtNum(r.recovVol)} sessions)
                    </span>
                  </td>
                  <td onClick={(e) => e.stopPropagation()} style={{ minWidth: 290 }}>
                    {isEditing ? (
                      <form
                        onSubmit={(e) => submitHypothesis(r, e)}
                        className="hyp-form"
                      >
                        <input
                          type="text"
                          placeholder="One-line PM hypothesis..."
                          value={draftText}
                          onChange={(e) => setDraftText(e.target.value)}
                          className="hyp-input"
                          autoFocus
                        />
                        <input
                          type="text"
                          placeholder="Experiment link (optional)"
                          value={draftUrl}
                          onChange={(e) => setDraftUrl(e.target.value)}
                          className="hyp-input"
                        />
                        <div style={{ display: 'flex', gap: 4 }}>
                          <button type="submit" className="btn">
                            Save
                          </button>
                          <button
                            type="button"
                            className="btn"
                            onClick={() => setDraftEdgeKey(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      </form>
                    ) : hyp ? (
                      <div className="hyp-saved">
                        <div className="hyp-line">
                          Hypothesis: {hyp.hypothesis}{' '}
                          <a
                            href={hyp.experiment_url}
                            target="_blank"
                            rel="noreferrer"
                            className="exp-link"
                          >
                            [Exp Link]
                          </a>
                        </div>
                        <div className="hyp-ba">
                          Before: {fmtNum(hyp.before_lost_volume)} lost (
                          {hyp.before_dropoff_pct}%) → After:{' '}
                          {fmtNum(hyp.after_lost_volume)} lost (
                          {hyp.after_dropoff_pct}%)
                          {hyp.before_lost_volume != null &&
                            hyp.after_lost_volume != null && (
                              <span
                                className={`delta ${
                                  hyp.after_lost_volume <= hyp.before_lost_volume
                                    ? 'up'
                                    : 'down'
                                }`}
                                style={{ marginLeft: 6 }}
                              >
                                {hyp.after_lost_volume - hyp.before_lost_volume > 0
                                  ? '+'
                                  : ''}
                                {fmtNum(
                                  hyp.after_lost_volume - hyp.before_lost_volume
                                )}{' '}
                                lost
                              </span>
                            )}
                        </div>
                        <div style={{ display: 'flex', gap: 4, marginTop: 3 }}>
                          <button
                            className="btn"
                            onClick={(e) => openHypothesisEditor(r, e)}
                          >
                            Edit
                          </button>
                          <button
                            className="btn"
                            onClick={() => onDeleteHypothesis(r.edgeKey)}
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        className="btn"
                        onClick={(e) => openHypothesisEditor(r, e)}
                      >
                        + Log Hypothesis
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}
