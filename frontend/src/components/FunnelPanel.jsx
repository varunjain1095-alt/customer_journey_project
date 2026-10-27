import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

function EChart({ option, className }) {
  const ref = useRef(null)
  useEffect(() => {
    const chart = echarts.init(ref.current)
    chart.setOption(option)
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => { window.removeEventListener('resize', onResize); chart.dispose() }
  }, [option])
  return <div ref={ref} className={className} />
}

export function FunnelChart({ funnel }) {
  const steps = funnel.steps
  const n = steps.length
  const isTiny = n <= 2
  let worst = -1, worstIdx = -1
  steps.forEach((s, i) => { if (i > 0 && s.dropoff_pct > worst) { worst = s.dropoff_pct; worstIdx = i } })
  const worstName = worstIdx > 0 ? `${steps[worstIdx - 1].event_type} → ${steps[worstIdx].event_type}` : ''
  const option = {
    title: worstIdx > 0 ? {
      text: `Biggest drop-off: ${worstName}  (−${steps[worstIdx].dropoff_pct.toFixed(1)}%)`,
      left: 'center', top: 6,
      textStyle: { fontSize: 12, color: '#dc2626', fontWeight: 600 },
    } : undefined,
    tooltip: {
      formatter: (p) => `${p.data.name}<br/>${p.data.value.toLocaleString()} sessions<br/>${p.data.conv}% of step 1 · drop ${p.data.drop}%`,
    },
    series: [{
      type: 'funnel', top: 44, bottom: 10, left: isTiny ? '30%' : '8%', width: isTiny ? '40%' : '66%', sort: 'none', gap: 2, minSize: isTiny ? '20%' : '0%',
      label: { position: 'right', formatter: (p) => `${p.data.name}: ${p.data.conv}%`, fontSize: 11, color: '#334155' },
      labelLine: { length: 16 },
      itemStyle: { borderColor: '#fff', borderWidth: 1 },
      data: steps.map((s, i) => ({
        name: s.event_type, value: s.count,
        conv: s.conversion_pct.toFixed(1), drop: s.dropoff_pct.toFixed(1),
        itemStyle: i === worstIdx ? { borderColor: '#dc2626', borderWidth: 2 } : undefined,
      })),
    }],
  }
  return <EChart option={option} className="funnel" />
}

export function BarChart({ title, dist }) {
  const sorted = [...dist].sort((a, b) => a.pct - b.pct) // largest ends up on top
  const maxPct = Math.max(...sorted.map((d) => d.pct))
  const option = {
    title: { text: title, textStyle: { fontSize: 11, color: '#64748b', fontWeight: 600 }, left: 0, top: 0 },
    grid: { top: 30, bottom: 6, left: 4, right: 46, containLabel: true },
    xAxis: { type: 'value', max: 100, show: false },
    yAxis: {
      type: 'category', data: sorted.map((d) => d.value),
      axisLabel: { fontSize: 11, color: '#334155' },
      axisLine: { show: false }, axisTick: { show: false },
    },
    series: [{
      type: 'bar', barWidth: '58%',
      data: sorted.map((d) => ({
        value: d.pct,
        itemStyle: { color: d.pct === maxPct ? '#ff7f0e' : '#cbd5e1', borderRadius: [0, 4, 4, 0] },
      })),
      label: { show: true, position: 'right', formatter: (p) => `${p.value.toFixed(0)}%`,
               fontSize: 10, color: '#64748b' },
    }],
  }
  return <EChart option={option} className="chart" />
}

export default function FunnelPanel({ title, children }) {
  return <div className="panel"><h2>{title}</h2>{children}</div>
}
