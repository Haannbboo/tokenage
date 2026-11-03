import { useEffect, useRef, useState } from 'react'
import { formatNumber, formatTime } from '../utils'
import { t } from '../i18n'

type ContextPoint = { request: number; ts: number; context_tokens: number }
export type SessionContext = { points: ContextPoint[]; peak_tokens: number; request_count: number }
const compact = (value: number) => value >= 1000 ? `${Math.round(value / 1000)}k` : String(value)

export function SessionContextChart({ data, tz }: { data: SessionContext; tz?: string }) {
  const [hover, setHover] = useState<number | null>(null)
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
  const active = hover === null ? null : points?.[hover]
  const tooltip = active && `${t('Request')} ${formatNumber(active.request)} · ${formatTime(active.ts, tz)} · ${formatNumber(active.context_tokens)}`
  const tooltipWidth = Math.min(width - 8, (tooltip?.length || 0) * 7 + 12)
  return <div className="session-context" ref={wrapper}>{points?.length >= 2 && peak > 0 && peakPoint && <><strong>{t('Context size')}</strong><svg viewBox={`0 0 ${width} 200`} role="img" aria-label={t('Context size')} fontSize="12" onMouseLeave={() => setHover(null)} onMouseMove={e => {
    const rect = e.currentTarget.getBoundingClientRect()
    const target = 1 + Math.max(0, Math.min(1, ((e.clientX - rect.left) / rect.width * width - left) / (right - left))) * (count - 1)
    let best = 0
    points.forEach((p, i) => { if (Math.abs(p.request - target) < Math.abs(points[best].request - target)) best = i })
    setHover(best)
  }}>
    {[0, 1, 2, 3, 4].map(i => <g key={i}><line x1={left} x2={right} y1={y(i * tick)} y2={y(i * tick)} stroke="var(--border-color)" /><text x={left - 6} y={y(i * tick) + 4} textAnchor="end">{i * tick >= 1000 ? `${i * tick / 1000}k` : i * tick}</text></g>)}
    <path d={`${line} L${x(points[points.length - 1].request)},165 L${x(points[0].request)},165 Z`} fill="var(--color-green)" opacity="0.08" /><path d={line} fill="none" stroke="var(--color-green)" strokeWidth="2" />
    <circle cx={x(peakPoint.request)} cy={y(peak)} r="4" fill="var(--color-green)" /><text x={Math.max(85, Math.min(width - 50, x(peakPoint.request)))} y={Math.max(14, y(peak) - 9)} textAnchor="middle">{t('Peak')} {compact(peak)}</text>
    {[1, Math.ceil((count + 1) / 2), count].map((request, i) => <text key={i} x={[left, (left + right) / 2, right][i]} y="187" textAnchor={(['start', 'middle', 'end'] as const)[i]}>{t('Request')} {formatNumber(request)}</text>)}
    {active && <g><circle cx={x(active.request)} cy={y(active.context_tokens)} r="4" fill="var(--color-green)" /><rect x={Math.max(4, Math.min(width - tooltipWidth - 4, x(active.request) + 8))} y="12" width={tooltipWidth} height="22" rx="3" fill="var(--card-bg)" /><text x={Math.max(4, Math.min(width - tooltipWidth - 4, x(active.request) + 8)) + 6} y="27">{tooltip}</text></g>}
  </svg></>}</div>
}
