import { useEffect, useMemo, useRef, useState } from 'react'
import type { DailyUsage } from '../types'
import { formatCost, formatNumber, value, resolveTimezone } from '../utils'
import { useApp } from '../contexts/AppContext'
import { t } from '../i18n/index.ts'

export function DailyHeatmap({
  mode,
  data,
}: {
  mode: 'activity' | 'success-rate'
  data: DailyUsage[]
}) {
  const { timezone } = useApp()
  const tz = resolveTimezone(timezone)
  const [hoveredCell, setHoveredCell] = useState<{ date: string; data: DailyUsage | null; x: number; y: number } | null>(null)
  const [containerWidth, setContainerWidth] = useState(0)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) setContainerWidth(entry.contentRect.width)
    })
    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [])

  const allWeeks = useMemo(() => {
    const dataMap = new Map<string, DailyUsage>()
    for (const d of data) dataMap.set(d.period, d)

    const today = new Date()
    const endDate = new Date(today.getFullYear(), today.getMonth(), today.getDate())
    const startDate = new Date(endDate)
    startDate.setDate(startDate.getDate() - 728)
    while (startDate.getDay() !== 0) startDate.setDate(startDate.getDate() - 1)

    let max = 0
    const weeks: { date: Date; metric: number; data: DailyUsage | null }[][] = []
    const cursor = new Date(startDate)

    while (cursor <= endDate) {
      const week: { date: Date; metric: number; data: DailyUsage | null }[] = []
      for (let d = 0; d < 7; d++) {
        if (cursor > endDate) {
          week.push({ date: new Date(cursor), metric: -1, data: null })
        } else {
          const key = cursor.toISOString().split('T')[0]
          const dayData = dataMap.get(key) ?? null
          let metric = -1
          if (dayData) {
            if (mode === 'activity') {
              metric = value(dayData.total_tokens)
              if (metric > max) max = metric
            } else {
              const total = value(dayData.requests)
              const failed = value(dayData.failed_requests)
              if (total > 0) metric = ((total - failed) / total) * 100
            }
          }
          week.push({ date: new Date(cursor), metric, data: dayData })
        }
        cursor.setDate(cursor.getDate() + 1)
      }
      weeks.push(week)
    }
    return { weeks, maxVal: max }
  }, [data, mode])

  const cellSize = 13
  const gap = 3
  const leftPad = 36
  const topPad = 20
  const step = cellSize + gap

  const visibleCount = Math.max(1, Math.min(allWeeks.weeks.length, Math.floor((containerWidth - leftPad - 8) / step)))
  const visibleWeeks = allWeeks.weeks.slice(-visibleCount)

  const monthLabels = useMemo(() => {
    const labels: { label: string; col: number }[] = []
    let lastMonthKey = ''
    for (let wi = 0; wi < visibleWeeks.length; wi++) {
      const firstDay = visibleWeeks[wi][0]
      if (firstDay && firstDay.metric >= 0) {
        // Derive month key from tz-aware formatting to match the label
        const monthKey = new Intl.DateTimeFormat('en-US', { timeZone: tz, year: 'numeric', month: 'numeric' }).format(firstDay.date)
        if (monthKey !== lastMonthKey) {
          labels.push({ label: firstDay.date.toLocaleString(undefined, { month: 'short', timeZone: tz }), col: wi })
          lastMonthKey = monthKey
        }
      }
    }
    return labels
  }, [visibleWeeks, tz])

  function getColor(metric: number): string {
    if (metric < 0) return 'transparent'
    if (mode === 'activity') {
      if (metric === 0) return 'var(--heatmap-empty)'
      const ratio = Math.min(metric / (allWeeks.maxVal || 1), 1)
      if (ratio < 0.25) return 'var(--activity-1)'
      if (ratio < 0.5) return 'var(--activity-2)'
      if (ratio < 0.75) return 'var(--activity-3)'
      return 'var(--activity-4)'
    } else {
      if (metric >= 100) return 'var(--heat-4)'
      if (metric >= 99) return 'var(--heat-3)'
      if (metric >= 95) return 'var(--heat-2)'
      if (metric >= 90) return 'var(--color-yellow)'
      if (metric >= 80) return 'var(--color-pink)'
      return 'var(--badge-error-text)'
    }
  }

  const gridWidth = visibleWeeks.length * step
  const gridHeight = 7 * step

  const title = mode === 'activity' ? t('Daily Activity') : t('Success Rate')
  const legendColors = mode === 'activity'
    ? ['var(--heatmap-empty)', 'var(--activity-1)', 'var(--activity-2)', 'var(--activity-3)', 'var(--activity-4)']
    : ['var(--heat-4)', 'var(--heat-3)', 'var(--heat-2)', 'var(--color-yellow)', 'var(--color-pink)', 'var(--badge-error-text)']
  const legendStart = mode === 'activity' ? t('Less') : t('100%')
  const legendEnd = mode === 'activity' ? t('More') : t('Fail')

  return (
    <div ref={containerRef} className="widget" style={{ width: '100%', flex: 1, minHeight: 0, position: 'relative', overflow: 'visible', display: 'flex', flexDirection: 'column' }}>
      <div className="widget-header">
        <span>{title}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', marginLeft: 'auto', fontSize: '10px', color: 'var(--text-muted)' }}>
          <span>{legendStart}</span>
          {legendColors.map(c => (
            <div key={c} style={{ width: '11px', height: '11px', borderRadius: '2px', background: c, border: '1px solid var(--heatmap-cell-border)' }} />
          ))}
          <span>{legendEnd}</span>
        </div>
      </div>
      <div style={{ padding: '8px 0 12px', flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <svg viewBox={`0 0 ${leftPad + gridWidth + 8} ${topPad + gridHeight + 4}`} style={{ display: 'block', width: '100%', height: '100%' }} preserveAspectRatio="none">
          {monthLabels.map((m, i) => (
            <text key={i} x={leftPad + m.col * step} y={14} fontSize={10} fill="var(--heatmap-label)" fontWeight={500}>
              {m.label}
            </text>
          ))}
          {[1, 3, 5].map(d => (
            <text key={d} x={0} y={topPad + d * step + cellSize - 2} fontSize={10} fill="var(--heatmap-label)" fontWeight={500}>
              {[t('Sun'), t('Mon'), t('Tue'), t('Wed'), t('Thu'), t('Fri'), t('Sat')][d]}
            </text>
          ))}
          {visibleWeeks.map((week, wi) =>
            week.map((day, di) => {
              const x = leftPad + wi * step
              const y = topPad + di * step
              const isNoData = day.metric < 0
              return (
                <rect
                  key={`${wi}-${di}`}
                  x={x}
                  y={y}
                  width={cellSize}
                  height={cellSize}
                  rx={2}
                  ry={2}
                  fill={isNoData ? 'var(--heatmap-empty)' : getColor(day.metric)}
                  stroke={hoveredCell?.date === day.date.toISOString().split('T')[0] ? 'var(--text-primary)' : 'var(--heatmap-cell-border)'}
                  strokeWidth={hoveredCell?.date === day.date.toISOString().split('T')[0] ? 2 : 1}
                  style={{ cursor: 'pointer', transition: 'stroke 0.1s' }}
                  onMouseEnter={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect()
                    const parent = containerRef.current!.getBoundingClientRect()
                    setHoveredCell({
                      date: day.date.toISOString().split('T')[0],
                      data: day.data,
                      x: rect.left - parent.left + cellSize / 2,
                      y: rect.top - parent.top,
                    })
                  }}
                  onMouseLeave={() => setHoveredCell(null)}
                />
              )
            })
          )}
        </svg>
      </div>
      {hoveredCell && (() => {
        const d = hoveredCell.data
        // Parse YYYY-MM-DD directly to avoid browser timezone shifting
        const [yr, mo, dy] = hoveredCell.date.split('-').map(Number)
        const dateObj = new Date(Date.UTC(yr, mo - 1, dy, 12, 0, 0))
        const dateLabel = dateObj.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: tz })

        let content: React.ReactNode
        if (mode === 'activity') {
          const total = d ? value(d.total_tokens) : 0
          if (total === 0) {
            content = <div style={{ color: 'var(--text-muted)' }}>{t('No activity')}</div>
          } else {
            const hCached = d ? value(d.cached_tokens) : 0
            const hInput = d ? Math.max(0, value(d.prompt_tokens) - hCached) : 0
            const hOutput = d ? value(d.completion_tokens) : 0
            const requests = d ? value(d.requests) : 0
            const cost = d ? value(d.total_cost_usd) : 0
            content = (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginBottom: '3px' }}>
                  <span style={{ color: 'var(--text-muted)' }}>{t('Input:')}</span>
                  <span style={{ fontWeight: 600 }}>{formatNumber(hInput)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginBottom: '3px' }}>
                  <span style={{ color: 'var(--color-green)' }}>{t('Cached:')}</span>
                  <span style={{ fontWeight: 600 }}>{formatNumber(hCached)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginBottom: '3px' }}>
                  <span style={{ color: 'var(--color-blue)' }}>{t('Output:')}</span>
                  <span style={{ fontWeight: 600 }}>{formatNumber(hOutput)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginTop: '6px', paddingTop: '6px', borderTop: '1px solid var(--border-color)' }}>
                  <span style={{ fontWeight: 700 }}>{t('Total:')}</span>
                  <span style={{ fontWeight: 800 }}>{formatNumber(total)}</span>
                </div>
                {cost > 0 && (
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginTop: '3px' }}>
                    <span style={{ color: 'var(--text-muted)' }}>{t('Cost:')}</span>
                    <span style={{ fontWeight: 800 }}>{formatCost(cost)}</span>
                  </div>
                )}
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginTop: '3px' }}>
                  <span style={{ color: 'var(--text-muted)' }}>{t('Requests:')}</span>
                  <span style={{ fontWeight: 600 }}>{formatNumber(requests)}</span>
                </div>
              </>
            )
          }
        } else {
          const total = d ? value(d.requests) : 0
          if (total === 0) {
            content = <div style={{ color: 'var(--text-muted)' }}>{t('No requests')}</div>
          } else {
            const failed = d ? value(d.failed_requests) : 0
            const successful = d ? value(d.successful_requests) : 0
            const rate = ((total - failed) / total * 100)
            content = (
              <>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginBottom: '3px' }}>
                  <span style={{ color: 'var(--text-muted)' }}>{t('Success Rate:')}</span>
                  <span style={{ fontWeight: 700, color: rate >= 99 ? 'var(--badge-success-text)' : rate >= 90 ? 'var(--badge-warn-text)' : 'var(--badge-error-text)' }}>{rate.toFixed(1)}%</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginBottom: '3px' }}>
                  <span style={{ color: 'var(--badge-success-text)' }}>{t('Successful:')}</span>
                  <span style={{ fontWeight: 600 }}>{formatNumber(successful)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginBottom: '3px' }}>
                  <span style={{ color: 'var(--badge-error-text)' }}>{t('Failed:')}</span>
                  <span style={{ fontWeight: 600 }}>{formatNumber(failed)}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '16px', marginTop: '6px', paddingTop: '6px', borderTop: '1px solid var(--border-color)' }}>
                  <span style={{ fontWeight: 700 }}>{t('Total:')}</span>
                  <span style={{ fontWeight: 800 }}>{formatNumber(total)}</span>
                </div>
              </>
            )
          }
        }

        return (
          <div style={{
            position: 'absolute',
            left: hoveredCell.x,
            top: hoveredCell.y - 8,
            transform: 'translate(-50%, -100%)',
            backgroundColor: 'var(--card-bg)',
            color: 'var(--text-primary)',
            padding: '10px 14px',
            borderRadius: 'var(--r-md)',
            fontSize: '12px',
            zIndex: 200,
            pointerEvents: 'none',
            boxShadow: 'var(--shadow-menu)',
            minWidth: '180px',
            border: '1px solid var(--border-color)',
            whiteSpace: 'nowrap',
          }}>
            <div style={{ fontWeight: 600, marginBottom: '6px', borderBottom: '1px solid var(--border-color)', paddingBottom: '4px' }}>
              {dateLabel}
            </div>
            {content}
          </div>
        )
      })()}
    </div>
  )
}
