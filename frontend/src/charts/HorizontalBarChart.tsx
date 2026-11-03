import { useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { formatCompact, formatCost, formatLatency, formatNumber, formatRate, formatThroughput, ToolBadge } from '../utils'
import { ChartTooltip, TooltipDivider, TooltipRow, TooltipTitle } from './ChartTooltip'
import { tooltipPosition } from '../tooltip'
import { t } from '../i18n/index.ts'

export type BarItem = {
  name: string
  id?: string
  icon: React.ReactNode
  tokens: number
  promptTokens?: number
  completionTokens?: number
  cachedTokens?: number
  cacheWriteTokens?: number
  successful?: number
  failed?: number
  cost: number
  throughput?: number | null
  pricePerMillion?: number | null
  successRate?: number | null
  cacheHitRate?: number | null
  totalTime?: number | null
  avgTime?: number | null
  /** Dimmed identity colour: the dot and the bar. */
  color: string
}

export type Metric = 'tokens' | 'cost' | 'throughput' | 'successRate' | 'cacheHitRate' | 'count' | 'totalTime' | 'avgTime'

const additiveMetrics: Metric[] = ['tokens', 'cost', 'count', 'totalTime']

function metricValue(item: BarItem, metric: Metric) {
  if (metric === 'tokens' || metric === 'count') return item.tokens
  if (metric === 'cost') return item.cost
  if (metric === 'successRate') return item.successRate ?? 100
  if (metric === 'cacheHitRate') return item.cacheHitRate ?? 0
  if (metric === 'totalTime') return item.totalTime ?? 0
  if (metric === 'avgTime') return item.avgTime ?? 0
  return item.throughput ?? 0
}

function formatMetric(metric: Metric, v: number) {
  if (metric === 'tokens') return formatCompact(v)
  if (metric === 'count') return formatNumber(v)
  if (metric === 'cost') return formatCost(v, 2)
  if (metric === 'successRate' || metric === 'cacheHitRate') return `${v.toFixed(1)}%`
  if (metric === 'totalTime' || metric === 'avgTime') return formatLatency(v)
  return formatThroughput(v)
}

const pct = (part: number, whole: number) => `${(whole > 0 ? part / whole * 100 : 0).toFixed(1)}%`

/** The hover/focus box for one ranked row: context rows, a divider, then the ranked value. */
export function RankTooltipBody({ item, metric, rank, share, mono = false, toolBadge = false }: {
  item: BarItem
  metric: Metric
  rank: number
  /** The item's part of the list total for additive metrics, else null. */
  share: string | null
  mono?: boolean
  toolBadge?: boolean
}) {
  const cached = item.cachedTokens ?? 0
  const input = Math.max(0, (item.promptTokens ?? 0) - cached)
  const output = item.completionTokens ?? 0
  const shareRow = share != null && <TooltipRow label={t('Share')}>{share}</TooltipRow>
  let rows: React.ReactNode
  let totalLabel: string
  if (metric === 'tokens') {
    rows = <>
      <TooltipRow label={t('Cached')} swatch="var(--color-green)">{formatCompact(cached)}</TooltipRow>
      <TooltipRow label={t('Input')} swatch="var(--chart-2)">{formatCompact(input)}</TooltipRow>
      <TooltipRow label={t('Output')} swatch="var(--color-blue)">{formatCompact(output)}</TooltipRow>
    </>
    totalLabel = t('Total')
  } else if (metric === 'cost') {
    rows = <>
      <TooltipRow label={t('Tokens')}>{formatCompact(item.tokens)}</TooltipRow>
      {item.pricePerMillion != null && <TooltipRow label={t('Price')}>{formatRate(item.pricePerMillion)}</TooltipRow>}
      {shareRow}
    </>
    totalLabel = t('Cost')
  } else if (metric === 'throughput') {
    rows = <>
      <TooltipRow label={t('Output')} swatch="var(--color-blue)">{formatCompact(output)}</TooltipRow>
      <TooltipRow label={t('Tokens')}>{formatCompact(item.tokens)}</TooltipRow>
    </>
    totalLabel = t('Speed')
  } else if (metric === 'cacheHitRate') {
    rows = <>
      <TooltipRow label={t('Cached')} swatch="var(--color-green)">{formatCompact(cached)}</TooltipRow>
      <TooltipRow label={t('Input')} swatch="var(--chart-2)">{formatCompact(input)}</TooltipRow>
      {(item.cacheWriteTokens ?? 0) > 0 && <TooltipRow label={t('Cache write')} swatch="var(--color-yellow)">{formatCompact(item.cacheWriteTokens ?? 0)}</TooltipRow>}
    </>
    totalLabel = t('Hit rate')
  } else if (metric === 'successRate') {
    rows = <>
      <TooltipRow label={t('Successful')} swatch="var(--badge-success-text)">{formatNumber(item.successful ?? 0)}</TooltipRow>
      <TooltipRow label={t('Failed')} swatch="var(--badge-error-text)">{formatNumber(item.failed ?? 0)}</TooltipRow>
    </>
    totalLabel = t('Success rate')
  } else {
    // Tool calls: the two metrics not ranked by, then the share.
    rows = <>
      {metric !== 'count' && <TooltipRow label={t('Calls')}>{formatNumber(item.tokens)}</TooltipRow>}
      {metric !== 'totalTime' && <TooltipRow label={t('Total time')}>{formatLatency(item.totalTime ?? 0)}</TooltipRow>}
      {metric !== 'avgTime' && <TooltipRow label={t('Avg time')}>{formatLatency(item.avgTime ?? 0)}</TooltipRow>}
      {shareRow}
    </>
    totalLabel = metric === 'count' ? t('Calls') : metric === 'totalTime' ? t('Total time') : t('Avg time')
  }
  return <>
    <TooltipTitle>
      {toolBadge ? <ToolBadge name={item.name} /> : <>
        <span className="rank-mark">{item.icon ?? <i style={{ background: item.color }} />}</span>
        <span className={mono ? 'mono' : undefined}>{item.name}</span>
      </>}
      <small>#{rank}</small>
    </TooltipTitle>
    {rows}
    <TooltipDivider />
    <TooltipRow label={totalLabel} total>{formatMetric(metric, metricValue(item, metric))}</TooltipRow>
  </>
}

/** Place the tip above (or, without room, below) the row at pointer x, so it never covers the row. */
function placeTip(tip: HTMLElement, row: HTMLElement, x: number | null) {
  const r = row.getBoundingClientRect()
  const anchor = x ?? r.left + Math.min(r.width / 3, 160)
  const pos = tooltipPosition({ left: anchor, width: 0, top: r.top, bottom: r.bottom } as DOMRect, { width: tip.offsetWidth, height: tip.offsetHeight }, innerWidth, innerHeight)
  tip.style.left = `${pos.left}px`
  tip.style.top = `${pos.top}px`
  tip.classList.toggle('chart-tip-below', pos.below)
}

export function HorizontalBarChart({
  title,
  items,
  metric,
  mono = false,
  toolBadges = false,
  children,
}: {
  title: string
  items: BarItem[]
  mono?: boolean
  toolBadges?: boolean
  metric: Metric
  children?: React.ReactNode
}) {
  // The row shown as active: hovered, focused or tapped. x is the pointer, null when focused.
  const [active, setActive] = useState<{ index: number; x: number | null } | null>(null)
  const tipRef = useRef<HTMLDivElement>(null)
  const rowRefs = useRef(new Map<number, HTMLLIElement>())
  const tipId = useId()

  const sorted = [...items].sort((a, b) => metricValue(b, metric) - metricValue(a, metric))
  const maxValue = Math.max(...sorted.map((item) => metricValue(item, metric)), 1)
  const format = (v: number) => formatMetric(metric, v)
  // Shares only make sense for metrics that add up.
  const additive = additiveMetrics.includes(metric)
  const total = additive ? sorted.reduce((sum, item) => sum + metricValue(item, metric), 0) : 0
  const activeIndex = active?.index ?? -1
  const activeItem = activeIndex >= 0 ? sorted[activeIndex] : null

  useLayoutEffect(() => {
    const row = active && rowRefs.current.get(active.index)
    if (tipRef.current && row) placeTip(tipRef.current, row, active.x)
  })

  // Follow the row on scroll; hide once it leaves the list or the viewport.
  useLayoutEffect(() => {
    if (!active) return
    const follow = () => {
      const row = rowRefs.current.get(active.index)
      if (!row || !tipRef.current) return
      const r = row.getBoundingClientRect()
      const list = row.parentElement!.getBoundingClientRect()
      if (r.bottom <= list.top || r.top >= list.bottom || r.bottom <= 0 || r.top >= innerHeight) setActive(null)
      else placeTip(tipRef.current, row, active.x)
    }
    window.addEventListener('scroll', follow, true)
    return () => window.removeEventListener('scroll', follow, true)
  }, [active])

  return (
    <div className="widget top-list">
      <div className="widget-header">
        <span>{title}</span>
        {additive && total > 0 && <span className="widget-meta">{t('Total')} {format(total)}</span>}
      </div>
      {children}
      <div style={{ position: 'relative', flex: 1, display: 'flex', flexDirection: 'column' }}>
        {sorted.length === 0 ? (
          <div style={{ padding: '20px' }}>
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>{t('No data available')}</div>
          </div>
        ) : (
          <ol className={`rank-list${activeItem ? ' has-active' : ''}`} onMouseLeave={() => {
            const focused = [...rowRefs.current].find(([, row]) => row === document.activeElement)
            setActive(focused ? { index: focused[0], x: null } : null)
          }}>
          {sorted.map((s, index) => {
            const currentVal = metricValue(s, metric)
            const percentage = (currentVal / maxValue) * 100
            const sub = metric === 'cost' && s.pricePerMillion != null ? formatRate(s.pricePerMillion) : null
            const isActive = activeIndex === index

            return (
              <li
                key={`${s.name}-${index}`}
                ref={(el) => { if (el) rowRefs.current.set(index, el); else rowRefs.current.delete(index) }}
                className={`rank-row${toolBadges ? ' rank-row-tools' : ''}${isActive ? ' is-active' : ''}`}
                tabIndex={0}
                aria-describedby={isActive ? tipId : undefined}
                onMouseEnter={(e) => setActive({ index, x: e.clientX })}
                onMouseMove={(e) => { if (isActive && tipRef.current) placeTip(tipRef.current, e.currentTarget, e.clientX) }}
                onFocus={() => setActive((cur) => cur?.index === index ? cur : { index, x: null })}
                // A window blur (tab switch) keeps the row; focus moving elsewhere in the page hides it.
                onBlur={() => { if (document.hasFocus()) setActive((cur) => cur?.index === index ? null : cur) }}
                onClick={(e) => setActive({ index, x: e.clientX })}
                onKeyDown={(e) => { if (e.key === 'Escape') setActive(null) }}
              >
                <span className="rank-n">{index + 1}</span>
                {!toolBadges && <span className="rank-mark">{s.icon ?? <i style={{ background: s.color }} />}</span>}
                <span className="rank-name">
                  {toolBadges ? <ToolBadge name={s.name} /> : <span className={mono ? 'mono' : undefined}>{s.name}</span>}
                  {sub && <small>{sub}</small>}
                </span>
                <span className="rank-value">
                  {format(currentVal)}
                  {additive && total > 0 && <small>{(currentVal / total * 100).toFixed(1)}%</small>}
                </span>
                <span className="rank-bar"><i style={{ width: `${percentage}%`, background: s.color }} /></span>
              </li>
            )
            })
          }
          </ol>
        )}
      </div>
      {activeItem && createPortal(
        <ChartTooltip ref={tipRef} id={tipId} role="tooltip" className="rank-tip">
          <RankTooltipBody
            item={activeItem}
            metric={metric}
            rank={activeIndex + 1}
            mono={mono}
            toolBadge={toolBadges}
            share={additive ? pct(metricValue(activeItem, metric), total)
              : metric === 'avgTime' ? pct(activeItem.tokens, sorted.reduce((sum, item) => sum + item.tokens, 0)) : null}
          />
        </ChartTooltip>,
        document.body,
      )}
    </div>
  )
}
