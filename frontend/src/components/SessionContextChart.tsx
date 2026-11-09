import { useEffect, useRef, useState } from 'react'
import { formatCost, formatLatency, formatNumber, formatTime } from '../utils'
import { t } from '../i18n'
import { ChartTooltip, TooltipRow, TooltipTitle } from '../charts/ChartTooltip'
import type { EffortMix } from '../types'

type ContextPoint = { request: number; id: string; ts: number; model: string; cost_usd: number; latency_ms: number | null; context_tokens: number }
export type SessionContext = { points: ContextPoint[]; peak_tokens: number; request_count: number; effort_mix: EffortMix }
const compact = (value: number) => value >= 1000 ? `${Math.round(value / 1000)}k` : String(value)
const TIP_WIDTH = 240
// Half the width of the "Peak 187k" label, which the tooltip must not cover.
const PEAK_LABEL_HALF = 45

/** Context size per request. Hover is shared with the timeline and request rows by request id. */
export function SessionContextChart({ data, tz, hoveredId, hoveredTs, linkedRange, onHover, colorOf }: {
  data: SessionContext
  tz?: string
  hoveredId: string | null
  /** Time of the hovered request, for sessions whose chart is bucketed and lacks its point. */
  hoveredTs?: number | null
  /** Time span of the requests the timeline and list show; shaded when it is only part of the session. */
  linkedRange?: { from: number; to: number } | null
  onHover: (id: string | null) => void
  colorOf: (model: string) => string
}) {
  // The tooltip shows only while the pointer is on the chart; a hover from the
  // timeline or a row shows just the marker.
  const [pointerInside, setPointerInside] = useState(false)
  const [width, setWidth] = useState(800)
  const wrapper = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!wrapper.current || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => setWidth(Math.max(1, entries[0].contentRect.width)))
    observer.observe(wrapper.current)
    return () => observer.disconnect()
  }, [])
  const { points, peak_tokens: peak, request_count: count } = data
  const left = 48, right = Math.max(left + 1, width - 16)
  const rough = Math.max(peak / 4, 0.25)
  const step = 10 ** Math.floor(Math.log10(rough)) / 2
  const tick = Math.ceil(rough / step) * step
  const x = (request: number) => left + (request - 1) / Math.max(1, count - 1) * (right - left)
  const y = (value: number) => 165 - value / (tick * 4) * 125
  const line = points?.map((p, i) => `${i ? 'L' : 'M'}${x(p.request)},${y(p.context_tokens)}`).join(' ')
  const peakPoint = points?.find(p => p.context_tokens === peak)
  // The hovered request's own point, else (bucketed sessions) the point nearest in time.
  const active = hoveredId === null ? null : points?.find(p => p.id === hoveredId)
    ?? (hoveredTs == null || !points?.length ? null : points.reduce((a, b) => Math.abs(b.ts - hoveredTs) < Math.abs(a.ts - hoveredTs) ? b : a))
  if (!(points?.length >= 2 && peak > 0 && peakPoint)) return <div className="session-context" ref={wrapper} />

  // Matched by time against the chart points, so a bucketed chart is off by at most one bucket.
  const linkedFrom = linkedRange ? points.find(p => p.ts >= linkedRange.from) ?? points[points.length - 1] : null
  const linkedTo = linkedRange ? [...points].reverse().find(p => p.ts <= linkedRange.to) ?? points[0] : null
  const peakX = Math.max(85, Math.min(width - 50, x(peakPoint.request)))
  const peakY = Math.max(14, y(peak) - 9)
  // Beside the point, flipping sides near an edge; below the peak label when
  // neither side keeps clear of it.
  let tip: { left: number; top: number } | null = null
  if (active && pointerInside) {
    const px = x(active.request)
    const fits = (l: number) => l >= 4 && l + TIP_WIDTH <= width - 4
    const clearOfPeak = (l: number) => l + TIP_WIDTH < peakX - PEAK_LABEL_HALF || l > peakX + PEAK_LABEL_HALF
    const sides = [px + 12, px - 12 - TIP_WIDTH].filter(fits)
    const side = sides.find(clearOfPeak)
    tip = side !== undefined
      ? { left: side, top: 4 }
      : { left: sides[0] ?? Math.max(4, Math.min(width - TIP_WIDTH - 4, px + 12)), top: peakY + 8 }
  }

  return <div className="session-context" ref={wrapper}><strong>{t('Context size')}</strong><div className="session-context-plot"><svg viewBox={`0 0 ${width} 200`} role="img" aria-label={t('Context size')} fontSize="12" onMouseLeave={() => { setPointerInside(false); onHover(null) }} onMouseMove={e => {
    const rect = e.currentTarget.getBoundingClientRect()
    const target = 1 + Math.max(0, Math.min(1, ((e.clientX - rect.left) / rect.width * width - left) / (right - left))) * (count - 1)
    let best = 0
    points.forEach((p, i) => { if (Math.abs(p.request - target) < Math.abs(points[best].request - target)) best = i })
    setPointerInside(true)
    if (points[best].id !== hoveredId) onHover(points[best].id)
  }}>
    {linkedFrom && linkedTo && <rect className="session-context-linked" x={x(linkedFrom.request) - 3} y={y(tick * 4)} width={Math.max(6, x(linkedTo.request) - x(linkedFrom.request) + 6)} height={165 - y(tick * 4)} fill="var(--text-muted)" opacity="0.1" />}
    {[0, 1, 2, 3, 4].map(i => <g key={i}><line x1={left} x2={right} y1={y(i * tick)} y2={y(i * tick)} stroke="var(--border-color)" /><text x={left - 6} y={y(i * tick) + 4} textAnchor="end">{i * tick >= 1000 ? `${i * tick / 1000}k` : i * tick}</text></g>)}
    <path d={`${line} L${x(points[points.length - 1].request)},165 L${x(points[0].request)},165 Z`} fill="var(--color-green)" opacity="0.08" /><path d={line} fill="none" stroke="var(--color-green)" strokeWidth="2" />
    <circle cx={x(peakPoint.request)} cy={y(peak)} r="4" fill="var(--color-green)" /><text x={peakX} y={peakY} textAnchor="middle">{t('Peak')} {compact(peak)}</text>
    {[1, Math.ceil((count + 1) / 2), count].map((request, i) => <text key={i} x={[left, (left + right) / 2, right][i]} y="187" textAnchor={(['start', 'middle', 'end'] as const)[i]}>{t('Request')} {formatNumber(request)}</text>)}
    {active && <g className="session-context-marker"><line x1={x(active.request)} x2={x(active.request)} y1={y(tick * 4)} y2={165} stroke="var(--text-muted)" strokeDasharray="3 3" /><circle cx={x(active.request)} cy={y(active.context_tokens)} r="4.5" fill="var(--card-bg)" stroke="var(--color-green)" strokeWidth="2" /></g>}
  </svg>
  {active && tip && <ChartTooltip style={{ position: 'absolute', left: tip.left, top: tip.top, width: TIP_WIDTH }}>
    <TooltipTitle>{t('Request')} {formatNumber(active.request)}</TooltipTitle>
    <TooltipRow label={t('Time')}>{formatTime(active.ts, tz)}</TooltipRow>
    <TooltipRow label={t('Context size')}>{formatNumber(active.context_tokens)}</TooltipRow>
    <TooltipRow label={t('Model')} swatch={colorOf(active.model)}><span className="session-context-model">{active.model}</span></TooltipRow>
    <TooltipRow label={t('Cost')}>{formatCost(active.cost_usd, 3)}</TooltipRow>
    {active.latency_ms != null && <TooltipRow label={t('Latency')}>{formatLatency(active.latency_ms)}</TooltipRow>}
  </ChartTooltip>}
  </div></div>
}
