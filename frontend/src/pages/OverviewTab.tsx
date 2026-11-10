import { useMemo, useState } from 'react'
import { useCountUp } from '../useCountUp'
import { TrendChart } from '../charts/TrendChart'
import { ReadState, type ReadError } from '../components/ReadState'
import { TopUsageChart } from '../charts/TopUsageChart'
import { ToolCallsChart } from '../charts/ToolCallsChart'
import { DailyHeatmap } from '../charts/DailyHeatmap'
import { EmptyGuide } from '../components/EmptyGuide'
import { StatTile } from '../components/StatTile'
import { getLang, LANG_LOCALES, t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import {
  formatCompact, formatCost, formatNumber, formatRate, formatThroughput,
  value, cacheHitRate, resolveTimezone, wallClockMs,
} from '../utils'
import { describePeriod, periodLabelText } from '../periodLabels'
import { fillPeriodGaps } from '../periodGaps'
import { rangeGranularity, rangePeriodCount } from '../dateRange.ts'
import type { DateRangeOption, PeriodGranularity, UsageRow } from '../types'

const TREND_TITLES: Record<PeriodGranularity, string> = {
  '15m': '15-Minute Usage Trend',
  hour: 'Hourly Usage Trend',
  day: 'Daily Usage Trend',
}

type OverviewTabProps = {
  theme: any
  summary: any[]
  dailyUsage: any[]
  heatmapData: any[]
  dashboardInitialLoading: boolean
  dashboardRefreshing: boolean
  summaryPending: boolean
  dashboardError: string | null
  dailyError: boolean
  dailyPending: boolean
  heatmapError: ReadError
  /** `heatmapData` still belongs to another filter. */
  heatmapPending: boolean
  onRetry: () => void
  dateRange: DateRangeOption
  dashboardFilterParams: any
  totals: any
  showFirstRunOnboarding: boolean
  verificationResult: UsageRow | null
  onFinishOnboarding: () => void
  onNavigateToLogs: (filters?: any) => void
}

export function OverviewTab({
  theme,
  summary,
  dailyUsage,
  heatmapData,
  dashboardInitialLoading,
  dashboardRefreshing,
  summaryPending,
  dashboardError,
  dailyError,
  dailyPending,
  heatmapError,
  heatmapPending,
  onRetry,
  dateRange,
  dashboardFilterParams,
  totals,
  showFirstRunOnboarding,
  verificationResult,
  onFinishOnboarding,
  onNavigateToLogs,
}: OverviewTabProps) {
  const animatedTotalTokens = useCountUp(dashboardInitialLoading ? 0 : totals.totalTokens)
  const animatedRequests = useCountUp(dashboardInitialLoading ? 0 : totals.requests)
  const animatedCost = useCountUp(dashboardInitialLoading ? 0 : totals.totalCost)
  const animatedThroughput = useCountUp(dashboardInitialLoading ? 0 : totals.avgThroughput)
  const animatedHitRate = useCountUp(
    dashboardInitialLoading ? 0 : cacheHitRate(totals.cachedTokens, totals.promptTokens, totals.cacheCreationTokens),
  )
  const totalInputTokens = (totals.promptTokens || 0) + (totals.cacheCreationTokens || 0)

  // One cursor for the whole row: hovering a point shows that period in every
  // tile, so the metrics can be read side by side.
  const [cursorIdx, setCursorIdx] = useState<number | null>(null)

  // /usage/daily only returns periods that have usage; the missing buckets are
  // filled in so the sparklines sit on a real time axis.
  const granularity = rangeGranularity(dateRange)
  // Rows of another view would draw the old range's sparklines: none until the new ones land.
  const filledUsage = useMemo(() => fillPeriodGaps(dailyPending ? [] : dailyUsage, granularity), [dailyUsage, dailyPending, granularity])

  // The hovered period is a wall-clock label in the display timezone, so "now"
  // has to be read in that same clock.
  const { timezone } = useApp()
  const nowWallMs = wallClockMs(new Date(), resolveTimezone(timezone))
  const hoveredPeriod = cursorIdx === null ? null : filledUsage[cursorIdx]?.period
  const hoverLabel = periodLabelText(
    hoveredPeriod ? describePeriod(hoveredPeriod, granularity, nowWallMs, LANG_LOCALES[getLang()]) : null,
  )

  const costPoints = filledUsage.map(d => ({ value: value(d.total_cost_usd), period: d.period, active: d.requests > 0 }))
  const tokenPoints = filledUsage.map(d => ({ value: value(d.total_tokens), period: d.period, active: d.requests > 0 }))
  const requestPoints = filledUsage.map(d => ({ value: d.requests, period: d.period, active: d.requests > 0 }))
  const hitRatePoints = filledUsage.map(d => ({
    value: cacheHitRate(d.cached_tokens, d.prompt_tokens, d.cache_creation_tokens),
    period: d.period,
    active: value(d.prompt_tokens) + value(d.cache_creation_tokens) > 0,
  }))
  const speedPoints = filledUsage.map(d => ({ value: value(d.avg_throughput), period: d.period, active: d.avg_throughput != null }))

  return (
    <>

      {dashboardError ? (
        <ReadState error={dashboardError} onRetry={onRetry} />
      ) : dashboardInitialLoading ? (
        <ReadState />
      ) : showFirstRunOnboarding ? (
        <EmptyGuide verificationResult={verificationResult} onViewLogs={() => onNavigateToLogs()} onDone={onFinishOnboarding} />
      ) : (
      <div className={`dashboard-refresh-surface ${dashboardRefreshing ? 'is-refreshing' : ''}`}>
      <div className="widgets-grid">
          <>
        <StatTile
          label={t('Estimated Cost')}
          color="var(--color-blue)"
          points={costPoints}
          animatedValue={animatedCost}
          format={(v) => formatCost(v, 2)}
          formatHover={(v) => formatCost(v, v < 0.1 ? 4 : 2)}
          cursorIdx={cursorIdx}
          hoverLabel={hoverLabel}
          onHover={setCursorIdx}
          pending={summaryPending}
        >
          {t('Avg $/M tokens:')} <span>{formatRate(totals.avgEffectivePricePerMillion)}</span>
        </StatTile>

        <StatTile
          label={t('Token Usage')}
          color="var(--color-yellow)"
          points={tokenPoints}
          animatedValue={animatedTotalTokens}
          // Whole tokens: the count-up passes fractions on the way.
          format={(v) => formatCompact(Math.round(v))}
          cursorIdx={cursorIdx}
          hoverLabel={hoverLabel}
          onHover={setCursorIdx}
          pending={summaryPending}
        >
          {/* cache_creation_tokens folds into "In" (fresh input, billed
              differently from cache-read) so In + Out reconciles with
              the Token Usage total above. */}
          {t('In:')} {formatCompact(totalInputTokens)} / {t('Out:')} {formatCompact(totals.completionTokens)}
        </StatTile>

        <StatTile
          label={t('Requests')}
          color="var(--color-pink)"
          points={requestPoints}
          animatedValue={animatedRequests}
          format={(v) => formatNumber(Math.round(v))}
          cursorIdx={cursorIdx}
          hoverLabel={hoverLabel}
          onHover={setCursorIdx}
          pending={summaryPending}
        >
          {t('Avg:')} <span>{formatCompact(totals.avgTokensPerRequest)} {t('tokens/req')}</span>
        </StatTile>

        <StatTile
          label={t('Cache Hit Rate')}
          color="var(--hue-green)"
          points={hitRatePoints}
          animatedValue={animatedHitRate}
          format={(v) => `${v.toFixed(1)}%`}
          scale="relative"
          cursorIdx={cursorIdx}
          hoverLabel={hoverLabel}
          onHover={setCursorIdx}
          pending={summaryPending}
        >
          {t('Cached:')} {formatCompact(totals.cachedTokens)} {t('of')} {formatCompact(totalInputTokens)} {t('tokens')}
        </StatTile>

        <StatTile
          label={t('Avg Speed')}
          color="var(--color-purple)"
          points={speedPoints}
          animatedValue={animatedThroughput}
          format={(v) => v > 0 ? formatThroughput(v) : '0.0 t/s'}
          cursorIdx={cursorIdx}
          hoverLabel={hoverLabel}
          onHover={setCursorIdx}
          pending={summaryPending}
        />
          </>
      </div>

      <div className="overview-split-row">
        <div style={{ flex: 1, minWidth: 0 }}>
          <TopUsageChart
            summary={summary}
            theme={theme}
            filterParams={dashboardFilterParams}
            summaryPending={summaryPending}
          />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <ToolCallsChart filterParams={dashboardFilterParams} />
        </div>
      </div>

      <div className="overview-split-row overview-trend-row">
        <div style={{ flex: '1 1 0', minWidth: 0 }}>
          <TrendChart
            data={dailyUsage}
            title={t(TREND_TITLES[granularity])}
            filterParams={dashboardFilterParams}
            granularity={granularity}
            periodCount={rangePeriodCount(dateRange)}
            showDots={dateRange !== 'all'}
            totals={dailyError ? 'failed' : dailyPending ? 'pending' : 'ready'}
            onRetryTotals={onRetry}
          />
        </div>
        <div style={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {heatmapError || heatmapPending ? (
              <div className="widget" style={{ flex: 1 }}>
                <ReadState error={heatmapError === 'failed' ? t('Failed to load activity') : null} onRetry={onRetry} />
              </div>
            ) : <>
              <DailyHeatmap mode="activity" data={heatmapData} />
              <DailyHeatmap mode="success-rate" data={heatmapData} />
            </>}
          </div>
      </div>
      </div>

      )}

    </>
  )
}
