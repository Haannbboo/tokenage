import { useMemo } from 'react'
import type { UsageRow } from '../types'
import { getModelTextColor } from '../model-badge'
import { formatTime, PALETTE } from '../utils'
import { t } from '../i18n'

export function lanePositions(rows: UsageRow[]) {
  const sorted = [...rows].sort((a, b) => a.ts / 1000 - (a.latency_ms ?? 0) - (b.ts / 1000 - (b.latency_ms ?? 0)))
  const start = sorted.length ? sorted[0].ts / 1000 - Math.max(sorted[0].latency_ms ?? 0, 0) : 0
  const marks = sorted.map(row => {
    const end = row.ts / 1000
    const width = Math.max(row.latency_ms ?? 0, 0)
    return { row, x: end - width - start, width }
  })
  const total = Math.max(1, ...marks.map(mark => mark.x + mark.width))
  return { marks, total, start }
}
export function laneLayout(rows: UsageRow[], width = 1000) {
  const lane = lanePositions(rows)
  let previousRight = -1
  const marks = lane.marks.map((mark, index) => {
    const desired = Math.max(0, mark.x / lane.total * width)
    const x = Math.max(desired, previousRight + 1)
    const next = lane.marks[index + 1]
    const scaled = Math.max(3, mark.width / lane.total * width)
    const markWidth = next && x + scaled + 1 > next.x / lane.total * width ? 3 : scaled
    previousRight = x + markWidth
    return { ...mark, px: x, pxWidth: markWidth }
  })
  const actualWidth = Math.max(width, previousRight)
  const ticks = Array.from({ length: 5 }, (_, i) => {
    const x = lane.total * i / 4
    return { x: x / lane.total * width, time: lane.start + x }
  })
  return { ...lane, marks, ticks, width: actualWidth }
}
function axisTime(ms: number, tz?: string) { return new Intl.DateTimeFormat(undefined, { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(ms)) }
export function SessionLane({ rows, selected, hovered, onHover, onSelect, tz }: { rows: UsageRow[]; selected: string | null; hovered?: string | null; onHover?: (id: string | null) => void; onSelect: (id: string) => void; tz?: string }) {
  const layout = useMemo(() => laneLayout(rows), [rows])
  const modelColors = useMemo(() => {
    const colors = new Map<string, string>(), used = new Set<string>()
    for (const model of [...new Set(rows.map(row => row.model))].sort()) {
      const preferred = getModelTextColor(model)
      const color = used.has(preferred) ? PALETTE.find(candidate => !used.has(candidate)) ?? preferred : preferred
      colors.set(model, color); used.add(color)
    }
    return colors
  }, [rows])
  return <div><div className="session-lane-heading"><span>{t('Timeline')}</span></div><div className="session-model-legend">{[...modelColors].map(([model, color]) => <span key={model}><i style={{ background: color }} />{model}</span>)}</div>
  <div className="session-lane-scroll"><div className="session-lane"><div className="session-lane-track">
    {layout.marks.map(({ row, px, pxWidth }) => <span key={row.id} className="session-mark-wrap" style={{ left: `${px / layout.width * 100}%`, width: `${pxWidth / layout.width * 100}%` }}>
      <button className={`session-mark${selected === row.id || hovered === row.id ? ' selected' : ''}${(row.status ?? 200) >= 400 ? ' error' : ''}`} style={{ background: (row.status ?? 200) >= 400 ? 'var(--color-red, #dc2626)' : modelColors.get(row.model) }} onMouseEnter={() => onHover?.(row.id)} onMouseLeave={() => onHover?.(null)} onFocus={() => onHover?.(row.id)} onBlur={() => onHover?.(null)} onClick={() => onSelect(row.id)} aria-label={`${formatTime(row.ts, tz)} ${row.model} ${row.status ?? 200}`} />

    </span>)}
    {layout.ticks.map((tick, i) => <span key={i} className="session-lane-tick" style={{ left: `${tick.x / layout.width * 100}%` }}>{axisTime(tick.time, tz)}</span>)}
  </div></div></div></div>
}
