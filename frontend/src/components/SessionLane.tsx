import { useMemo, useState } from 'react'
import type { UsageRow } from '../types'
import { getModelTextColor } from '../model-badge'
import { formatTime, PALETTE } from '../utils'
import { t } from '../i18n'

const MAX_GAP = 60_000
export function lanePositions(rows: UsageRow[], compact: boolean) {
  const sorted = [...rows].sort((a, b) => a.ts / 1000 - (a.latency_ms ?? 0) - (b.ts / 1000 - (b.latency_ms ?? 0)))
  let position = 0, previousEnd = 0
  const cuts: { x: number; start: number; end: number }[] = []
  const marks = sorted.map((row, i) => {
    const end = row.ts / 1000
    const start = end - Math.max(row.latency_ms ?? 0, 0)
    const gap = i ? Math.max(0, start - previousEnd) : 0
    const cut = compact && gap > MAX_GAP
    if (cut) cuts.push({ x: position, start: previousEnd, end: start })
    position += cut ? MAX_GAP : gap
    const x = position + Math.min(0, start - previousEnd) * (i ? 1 : 0)
    position += Math.max(0, end - Math.max(start, previousEnd || start))
    previousEnd = Math.max(previousEnd, end)
    return { row, x, width: Math.max(0, end - start), gap: cut ? gap : 0 }
  })
  return { marks, cuts, total: Math.max(position, 1), start: sorted.length ? sorted[0].ts / 1000 - Math.max(sorted[0].latency_ms ?? 0, 0) : 0 }
}
export function laneLayout(rows: UsageRow[], compact: boolean, width = 1000) {
  const lane = lanePositions(rows, compact)
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
    let time = lane.start + x
    for (const cut of lane.cuts) {
      if (x >= cut.x + MAX_GAP) time += cut.end - cut.start - MAX_GAP
      else if (x > cut.x) { time += (x - cut.x) / MAX_GAP * (cut.end - cut.start) - (x - cut.x); break }
    }
    return { x: x / lane.total * width, time }
  })
  return { ...lane, marks, ticks, width: actualWidth }
}
function axisTime(ms: number, tz?: string) { return new Intl.DateTimeFormat(undefined, { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(ms)) }
export function gapLength(ms: number) { const seconds = Math.floor(ms / 1000); const m = Math.floor(seconds / 60); return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m ${seconds % 60}s` }
export function SessionLane({ rows, selected, hovered, onHover, onSelect, tz }: { rows: UsageRow[]; selected: string | null; hovered?: string | null; onHover?: (id: string | null) => void; onSelect: (id: string) => void; tz?: string }) {
  const [compact, setCompact] = useState(() => { try { return localStorage.getItem('tokenage-session-gaps') !== 'real' } catch { return true } })
  const hasCuts = useMemo(() => lanePositions(rows, true).cuts.length > 0, [rows])
  const layout = useMemo(() => laneLayout(rows, compact), [rows, compact])
  const modelColors = useMemo(() => {
    const colors = new Map<string, string>(), used = new Set<string>()
    for (const model of [...new Set(rows.map(row => row.model))].sort()) {
      const preferred = getModelTextColor(model)
      const color = used.has(preferred) ? PALETTE.find(candidate => !used.has(candidate)) ?? preferred : preferred
      colors.set(model, color); used.add(color)
    }
    return colors
  }, [rows])
  return <div><div className="session-lane-heading"><span>{t('Timeline')}</span>{hasCuts && <div role="group" aria-label={t('Gap display')}>
    {[true, false].map(value => <button key={String(value)} className="btn-ghost" aria-pressed={compact === value} onClick={() => { setCompact(value); try { localStorage.setItem('tokenage-session-gaps', value ? 'compact' : 'real') } catch { /* storage unavailable */ } }}>{t(value ? 'Compact' : 'Real time')}</button>)}
  </div>}</div><div className="session-model-legend">{[...modelColors].map(([model, color]) => <span key={model}><i style={{ background: color }} />{model}</span>)}</div>
  <div className="session-lane-scroll"><div className="session-lane"><div className="session-lane-track">
    {layout.cuts.map(cut => <span key={cut.x} className="session-gap" style={{ left: `${(cut.x + MAX_GAP / 2) / layout.total * 100}%` }}>{gapLength(cut.end - cut.start)} {t('gap')}</span>)}
    {layout.marks.map(({ row, px, pxWidth }) => <span key={row.id} className="session-mark-wrap" style={{ left: `${px / layout.width * 100}%`, width: `${pxWidth / layout.width * 100}%` }}>
      <button className={`session-mark${selected === row.id || hovered === row.id ? ' selected' : ''}${(row.status ?? 200) >= 400 ? ' error' : ''}`} style={{ background: (row.status ?? 200) >= 400 ? 'var(--color-red, #dc2626)' : modelColors.get(row.model) }} onMouseEnter={() => onHover?.(row.id)} onMouseLeave={() => onHover?.(null)} onFocus={() => onHover?.(row.id)} onBlur={() => onHover?.(null)} onClick={() => onSelect(row.id)} aria-label={`${formatTime(row.ts, tz)} ${row.model} ${row.status ?? 200}`} />

    </span>)}
    {layout.ticks.map((tick, i) => <span key={i} className="session-lane-tick" style={{ left: `${tick.x / layout.width * 100}%` }}>{axisTime(tick.time, tz)}</span>)}
  </div></div></div></div>
}
