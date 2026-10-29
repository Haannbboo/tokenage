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
  color: string
  badgeBg: string
  badgeText: string
}

export type Metric = 'tokens' | 'cost' | 'throughput' | 'successRate' | 'cacheHitRate' | 'count' | 'totalTime' | 'avgTime'

export function HorizontalBarChart({
  title,
  icon,
  items,
  metric,
  children,
}: {
  title: string
  icon: string
  items: BarItem[]
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

  return (
    <div className="widget" style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div className="widget-header">
        <span>{icon} {title}</span>
      </div>
      <div style={{ position: 'relative', flex: 1, display: 'flex', flexDirection: 'column' }}>
        {metric === 'tokens' && hovered && (
          <ChartTooltip left="50%">
            <div style={{ fontWeight: 600, marginBottom: '8px', borderBottom: '1px solid var(--border-color)', paddingBottom: '4px', fontSize: '13px' }}>
              {hovered.name}
            </div>
            <TooltipRow label={t('Cached:')} labelColor="var(--color-green)">
              <span style={{ fontWeight: 600 }}>{formatCompact(hovered.cachedTokens ?? 0)}</span>
            </TooltipRow>
            <TooltipRow label={t('Input:')} labelColor="#94a3b8">
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
          <div style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px', overflowY: 'auto', maxHeight: '300px' }}>
          {sorted.map((s, index) => {
            const currentVal = getMetricValue(s)
            const percentage = (currentVal / maxValue) * 100

            return (
              <div
                key={`${s.name}-${index}`}
                style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}
                onMouseEnter={() => setHoveredItem(s.name)}
                onMouseLeave={() => setHoveredItem((current) => current === s.name ? null : current)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '12px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}>
                    {s.icon}
                    <span style={{
                      padding: '1px 6px',
                      borderRadius: '4px',
                      backgroundColor: s.badgeBg,
                      color: s.badgeText,
                      fontSize: '11px',
                      fontWeight: 600,
                    }}>{s.name}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px' }}>
                    <span style={{ color: 'var(--text-secondary)', fontWeight: 700 }}>
                      {metric === 'tokens' ? formatCompact(currentVal) : metric === 'count' ? formatNumber(currentVal) : metric === 'cost' ? formatCost(currentVal, 2) : metric === 'successRate' || metric === 'cacheHitRate' ? `${currentVal.toFixed(1)}%` : metric === 'totalTime' || metric === 'avgTime' ? formatLatency(currentVal) : formatThroughput(currentVal)}
                    </span>
                    {metric === 'cost' && s.pricePerMillion != null && (
                      <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
                        {formatRate(s.pricePerMillion)}
                      </span>
                    )}
                  </div>
                </div>
                <div className="progress-track" style={{ width: '100%', display: 'flex' }}>
                  <div
                    style={{ width: `${percentage}%`, height: '100%', background: s.color, transition: 'width 0.5s cubic-bezier(0.4, 0, 0.2, 1)' }}
                  />
                </div>
              </div>
            )
            })
          }
          </div>
        )}
      {/* Pinned to the card's bottom edge when the list is shorter than the card. */}
      {children && <div style={{ marginTop: 'auto' }}>{children}</div>}
    </div>
    </div>
  )
}
