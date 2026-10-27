import { BarChart } from './FunnelPanel.jsx'

const KPI_LABELS = {
  avg_session_duration: 'Avg session (s)',
  avg_order_value: 'Avg order value',
  avg_cart_abandonment_rate: 'Cart abandon %',
  avg_lifetime_value: 'Avg lifetime value',
}

const DIM_LABELS = {
  device_type: 'Device Type',
  traffic_source: 'Traffic Source',
  membership_status: 'Membership Status',
  country: 'Country',
  age_bucket: 'Age Bucket',
}

export function EdgeSegmentDrilldown({ edgeBreakdown }) {
  if (!edgeBreakdown || !edgeBreakdown.source_total) return null
  const { edge, source_total, advanced_count, lost_volume, baseline_drop_rate_pct, dimensions } = edgeBreakdown

  return (
    <div className="panel edge-drilldown-panel">
      <h2>
        Leak WHO Drill-Down: {edge.source} → {edge.target} (Baseline Drop Rate:{' '}
        <span style={{ color: '#dc2626' }}>{baseline_drop_rate_pct}%</span> ·{' '}
        {lost_volume.toLocaleString()} lost of {source_total.toLocaleString()} departures,{' '}
        {advanced_count.toLocaleString()} advanced)
      </h2>
      <div className="subnote" style={{ marginBottom: 10 }}>
        Joined on <code>CUSTOMERS.user_id</code> across <code>device_type</code>,{' '}
        <code>traffic_source</code>, <code>membership_status</code>, <code>country</code>, and{' '}
        <code>age_bucket</code>. Red delta indicates the segment drops off more than the overall edge baseline (
        {baseline_drop_rate_pct}%).
      </div>
      <div className="edge-dim-grid">
        {Object.entries(dimensions || {}).map(([dim, rows]) => (
          <div key={dim} className="edge-dim-card">
            <h3>{DIM_LABELS[dim] || dim}</h3>
            <table>
              <thead>
                <tr>
                  <th>Segment</th>
                  <th>Drop Rate</th>
                  <th>vs Baseline</th>
                  <th>Lost Vol</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const worse = r.delta_vs_baseline_pct > 0.25
                  const better = r.delta_vs_baseline_pct < -0.25
                  return (
                    <tr key={r.segment}>
                      <td>{r.segment}</td>
                      <td>{r.drop_rate_pct.toFixed(1)}%</td>
                      <td>
                        <span
                          className={`delta ${worse ? 'down' : better ? 'up' : ''}`}
                        >
                          {r.delta_vs_baseline_pct >= 0 ? '+' : ''}
                          {r.delta_vs_baseline_pct.toFixed(1)} pts
                        </span>
                      </td>
                      <td>
                        {r.lost_volume.toLocaleString()}{' '}
                        <span style={{ color: '#9ca3af', fontSize: 11 }}>
                          ({r.share_of_lost_pct.toFixed(0)}%)
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function SegmentPanel({ profile, label }) {
  if (profile.empty)
    return (
      <div className="panel">
        <h2>{label}: Segment Profile</h2>
        <p>No user sessions match this path filter.</p>
      </div>
    )
  return (
    <div className="panel">
      <h2>{label}: Path Cohort Profile ({profile.cohort_size.toLocaleString()} sessions)</h2>
      <div className="kpis">
        {Object.entries(profile.kpis).map(([k, v]) => (
          <div className="kpi" key={k}>
            <div className="v">{typeof v === 'number' ? v.toFixed(2) : v}</div>
            <div className="k">{KPI_LABELS[k] || k}</div>
          </div>
        ))}
      </div>
      <div className="dist-grid">
        {Object.entries(profile.distributions).map(([col, dist]) => (
          <BarChart key={col} title={col.replace(/_/g, ' ')} dist={dist} />
        ))}
      </div>
    </div>
  )
}
