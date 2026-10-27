export default function VariantTable({ variants, selectedPathId, onSelect,
  compareIds, onToggleCompare, stars, onToggleStar, datasetVersion }) {
  const maxPct = Math.max(...variants.top.map((v) => v.pct_of_sessions), 0.0001)
  return (
    <table>
      <thead>
        <tr>
          <th title="Compare 2-4 paths">⇄</th><th>#</th><th>Path</th>
          <th>Sessions</th><th>%</th><th>★</th>
        </tr>
      </thead>
      <tbody>
        {variants.top.map((v) => {
          const sel = v.path_id === selectedPathId
          const starInfo = stars[v.path_id]
          return (
            <tr key={v.path_id} className={`variant ${sel ? 'selected' : ''}`}
                onClick={() => onSelect(v)}>
              <td onClick={(e) => { e.stopPropagation(); onToggleCompare(v) }}>
                <input type="checkbox" checked={compareIds.has(v.path_id)} readOnly />
              </td>
              <td>{v.rank}</td>
              <td className="path">{v.path.join(' → ')}</td>
              <td>{v.session_count.toLocaleString()}</td>
              <td>
                <div className="pct-cell">
                  <span className="pct-bar" style={{ width: `${(v.pct_of_sessions / maxPct) * 100}%` }} />
                  <span className="pct-val">{v.pct_of_sessions.toFixed(2)}%</span>
                </div>
              </td>
              <td>
                <button className={`star ${starInfo ? 'on' : ''}`}
                  onClick={(e) => { e.stopPropagation(); onToggleStar(v) }}>
                  {starInfo ? '★' : '☆'}
                </button>
              </td>
            </tr>
          )
        })}
        {variants.other && (
          <tr className="other">
            <td></td><td>:</td>
            <td className="path">{variants.other.label}</td>
            <td>{variants.other.session_count.toLocaleString()}</td>
            <td>{variants.other.pct_of_sessions.toFixed(2)}%</td>
            <td></td>
          </tr>
        )}
      </tbody>
    </table>
  )
}
