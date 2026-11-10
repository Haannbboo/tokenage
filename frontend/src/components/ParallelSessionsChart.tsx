import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { formatTime } from '../utils'
import { t } from '../i18n'
import { ChartTooltip } from '../charts/ChartTooltip'
import type { SessionDistribution } from '../types'

type Point = SessionDistribution['over_time'][number]
const H = 140, TOP = 8, BOTTOM = 118, LEFT = 28

/** The first highest non-zero point. */
export function parallelPeak(points: Point[]) {
  return points.reduce<Point | null>((best, p) => p.session_count > (best?.session_count ?? 0) ? p : best, null)
}

const isDay = (point: Point) => /^\d{4}-\d{2}-\d{2}$/.test(point.period)

/** A point's time: the day alone for daily points. */
export function pointTime(point: Point, tz?: string) {
  if (!isDay(point)) return formatTime(point.key, tz)
  return new Intl.DateTimeFormat(undefined, { timeZone: tz, month: 'short', day: 'numeric' }).format(new Date(point.key / 1000))
}

/** Peak sessions with a request in the same 5-minute slot, one point per step. Read-only: a peak is not a set of sessions to filter by. */
export function ParallelSessionsChart({ points, tz }: { points: Point[]; tz?: string }) {
  const [width, setWidth] = useState(800)
  const [hovered, setHovered] = useState<number | null>(null)
  const wrapper = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!wrapper.current || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => setWidth(Math.max(1, entries[0].contentRect.width)))
    observer.observe(wrapper.current)
    return () => observer.disconnect()
  }, [])
  const peak = parallelPeak(points)
  const max = peak?.session_count ?? 0
  const step = Math.max(1, Math.ceil(max / 4)), top = Math.max(1, Math.ceil(max / step)) * step
  const right = Math.max(LEFT + 1, width - 8), n = points.length
  const x = (i: number) => LEFT + i / Math.max(1, n - 1) * (right - LEFT)
  const y = (v: number) => BOTTOM - v / top * (BOTTOM - TOP)
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p.session_count)}`).join('')
  // Daily points always get date labels, as in their tooltip.
  const daily = n > 0 && (isDay(points[0]) || points[n - 1].key - points[0].key > 36 * 3600e6)
  const axisTime = (p: Point) => new Intl.DateTimeFormat(undefined, { timeZone: tz, ...(daily ? { month: 'short', day: 'numeric' } : { hour: '2-digit', minute: '2-digit', hour12: false }) }).format(new Date(p.key / 1000))
  const labels = n > 1 ? [...new Set([0, 1, 2, 3].map(k => Math.round(k * (n - 1) / 3)))] : []
  const peakIndex = peak ? points.indexOf(peak) : -1
  const peakX = x(peakIndex), peakY = y(max)
  const active = hovered === null ? null : points[hovered]
  // Pointer events cover mouse and touch; a lifted finger keeps its point shown.
  const pick = (e: PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = (e.clientX - rect.left) / rect.width * width
    setHovered(Math.max(0, Math.min(n - 1, Math.round((px - LEFT) / (right - LEFT) * (n - 1)))))
  }
  return <section className="panel session-chart parallel-chart">
    <h3>{t('Parallel sessions')}</h3>
    <div className="parallel-chart-plot" ref={wrapper}>{n === 0 && <div className="sessions-empty-state">{t('No sessions yet.')}</div>}{n > 0 && <svg viewBox={`0 0 ${width} ${H}`} role="img" fontSize="11"
      aria-label={peak ? t('Parallel sessions: peak {n} at once at {time}').replace('{n}', String(peak.session_count)).replace('{time}', pointTime(peak, tz)) : t('Parallel sessions')}
      onPointerMove={pick} onPointerDown={pick} onPointerLeave={e => { if (e.pointerType === 'mouse') setHovered(null) }}>
      {Array.from({ length: top / step + 1 }, (_, i) => i * step).map(v => <g key={v}><line x1={LEFT} x2={right} y1={y(v)} y2={y(v)} stroke="var(--border-color)" opacity="0.5" /><text x={LEFT - 6} y={y(v) + 4} textAnchor="end">{v}</text></g>)}
      {labels.map((i, k) => <text key={i} x={x(i)} y={H - 4} textAnchor={k === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'}>{axisTime(points[i])}</text>)}
      {peak && <line className="parallel-chart-peak-line" x1={LEFT} x2={right} y1={peakY} y2={peakY} stroke="var(--hue-amber)" strokeDasharray="4 4" strokeWidth="1.25" opacity="0.75" />}
      <path d={`${line}L${x(n - 1)},${BOTTOM}L${x(0)},${BOTTOM}Z`} fill="var(--chart-2)" opacity="0.12" />
      <path d={line} fill="none" stroke="var(--chart-2)" strokeWidth="1.5" strokeLinejoin="round" />
      {peak && <circle className="parallel-chart-peak-dot" cx={peakX} cy={peakY} r="3.5" fill="var(--hue-amber)" />}
      {active && <g><line x1={x(hovered!)} x2={x(hovered!)} y1={TOP} y2={BOTTOM} stroke="var(--text-muted)" strokeDasharray="3 3" /><circle cx={x(hovered!)} cy={y(active.session_count)} r="3.5" fill="var(--card-bg)" stroke="var(--chart-2)" strokeWidth="2" /></g>}
    </svg>}
    {active && <ChartTooltip className="parallel-chart-tip" style={{ position: 'absolute', top: 0, left: Math.max(0, Math.min(width - 180, x(hovered!) - 90)), width: 'min(180px, 100%)' }}>
      {pointTime(active, tz)} · {t(active.session_count === 1 ? '1 session' : '{n} sessions').replace('{n}', String(active.session_count))}
    </ChartTooltip>}</div>
  </section>
}
