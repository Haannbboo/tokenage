import { useState } from 'react'
import { formatCompact, formatCost, formatLatency, formatNumber, formatRate, formatThroughput } from '../utils'
import { ChartTooltip, TooltipDivider, TooltipRow } from './ChartTooltip'
import { t } from '../i18n/index.ts'

export type BarItem = {
  name: string
  id?: string
  icon: React.ReactNode
  tokens: number
  promptTokens?: number
  completionTokens?: number
  cachedTokens?: number
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

export function HorizontalBarChart({
  title,
  items,
  metric,
  mono = false,
  children,
}: {
  title: string
  items: BarItem[]
  mono?: boolean
  metric: Metric
  children?: React.ReactNode
}) {
  const [hoveredItem, setHoveredItem] = useState<string | null>(null)

  const getMetricValue = (item: BarItem) => {
    if (metric === 'tokens' || metric === 'count') return item.tokens
    if (metric === 'cost') return item.cost
    if (metric === 'successRate') return item.successRate ?? 100
    if (metric === 'cacheHitRate') return item.cacheHitRate ?? 0
    if (metric === 'totalTime') return item.totalTime ?? 0
    if (metric === 'avgTime') return item.avgTime ?? 0
    return item.throughput ?? 0
  }

  const sorted = [...items]
    .sort((a, b) => getMetricValue(b) - getMetricValue(a))

  const maxValue = Math.max(
    ...sorted.map(getMetricValue),
    1
  )

  const hovered = hoveredItem ? sorted.find((item) => item.name === hoveredItem) ?? null : null

  const format = (v: number) => metric === 'tokens' ? formatCompact(v) : metric === 'count' ? formatNumber(v) : metric === 'cost' ? formatCost(v, 2) : metric === 'successRate' || metric === 'cacheHitRate' ? `${v.toFixed(1)}%` : metric === 'totalTime' || metric === 'avgTime' ? formatLatency(v) : formatThroughput(v)
  // Shares only make sense for metrics that add up.
  const additive = metric === 'tokens' || metric === 'cost' || metric === 'count' || metric === 'totalTime'
  const total = additive ? sorted.reduce((sum, item) => sum + getMetricValue(item), 0) : 0

  return (
    <div className="widget top-list">
      <div className="widget-header">
        <span>{title}</span>
        {additive && total > 0 && <span className="widget-meta">{t('Total')} {format(total)}</span>}
      </div>
      {children}
      <div style={{ position: 'relative', flex: 1, display: 'flex', flexDirection: 'column' }}>
        {metric === 'tokens' && hovered && (
          <ChartTooltip left="50%">
            <div style={{ fontWeight: 600, marginBottom: '8px', borderBottom: '1px solid var(--border-color)', paddingBottom: '4px', fontSize: '13px' }}>
              {hovered.name}
            </div>
            <TooltipRow label={t('Cached:')} labelColor="var(--color-green)">
              <span style={{ fontWeight: 600 }}>{formatCompact(hovered.cachedTokens ?? 0)}</span>
            </TooltipRow>
            <TooltipRow label={t('Input:')} labelColor="var(--chart-2)">
              <span style={{ fontWeight: 600 }}>{formatCompact(Math.max(0, (hovered.promptTokens ?? 0) - (hovered.cachedTokens ?? 0)))}</span>
            </TooltipRow>
            <TooltipRow label={t('Output:')} labelColor="var(--color-blue)">
              <span style={{ fontWeight: 600 }}>{formatCompact(hovered.completionTokens ?? 0)}</span>
            </TooltipRow>
            <TooltipDivider />
            <TooltipRow label={t('Total Tokens:')}>
              <span style={{ fontWeight: 800 }}>{formatCompact(hovered.tokens)}</span>
            </TooltipRow>
          </ChartTooltip>
        )}
        {sorted.length === 0 ? (
          <div style={{ padding: '20px' }}>
            <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-muted)' }}>{t('No data available')}</div>
          </div>
        ) : (
          <ol className="rank-list">
          {sorted.map((s, index) => {
            const currentVal = getMetricValue(s)
            const percentage = (currentVal / maxValue) * 100
            const sub = metric === 'cost' && s.pricePerMillion != null ? formatRate(s.pricePerMillion) : null

            return (
              <li
                key={`${s.name}-${index}`}
                className="rank-row"
                onMouseEnter={() => setHoveredItem(s.name)}
                onMouseLeave={() => setHoveredItem((current) => current === s.name ? null : current)}
              >
                <span className="rank-n">{index + 1}</span>
                <span className="rank-mark">{s.icon ?? <i style={{ background: s.color }} />}</span>
                <span className="rank-name" title={s.name}>
                  <span className={mono ? 'mono' : undefined}>{s.name}</span>
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
    </div>
  )
}
