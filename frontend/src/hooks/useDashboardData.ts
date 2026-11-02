import { appendModels } from '../modelFilter'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type { DailyUsage, DateRangeOption, UsageSummary } from '../types'
import { getSinceDate, rangeGranularity } from '../dateRange.ts'
import { getTimezoneOffset, resolveTimezone, FIXED_PROVIDER_COLORS, PALETTE, netThroughput } from '../utils'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'

type DashboardDataOptions = {
  dateRange?: DateRangeOption
  includeDaily?: boolean
  includeHeatmap?: boolean
  includeCount?: boolean
}

export function useDashboardData({ dateRange: dateRangeOverride, includeDaily = true, includeHeatmap = true, includeCount = true }: DashboardDataOptions = {}) {
  const { refreshTrigger, setError, activeFilter, activeSource, activeDevice, dateRange: appDateRange, timezone } = useApp()
  const dateRange = dateRangeOverride ?? appDateRange
  const tz = resolveTimezone(timezone)

  const [selectorSummary, setSelectorSummary] = useState<UsageSummary[]>([])
  const [summary, setSummary] = useState<UsageSummary[]>([])
  const [dailyUsage, setDailyUsage] = useState<DailyUsage[]>([])
  const [heatmapData, setHeatmapData] = useState<DailyUsage[]>([])
  const [totalTrackedEvents, setTotalTrackedEvents] = useState<number | null>(null)
  const [sources, setSources] = useState<string[]>([])
  const [dashboardInitialLoading, setDashboardInitialLoading] = useState(true)
  const [dashboardRefreshing, setDashboardRefreshing] = useState(false)

  const dashboardRequestRef = useRef(0)
  const dashboardHasLoadedRef = useRef(false)

  const providerColors = useMemo(() => {
    const allProviders = Array.from(new Set(summary.map(s => s.provider))).sort();
    const map: Record<string, string> = {};
    allProviders.forEach(p => {
      const lowP = p.toLowerCase();
      if (FIXED_PROVIDER_COLORS[lowP]) {
        map[p] = FIXED_PROVIDER_COLORS[lowP];
      }
    });
    let paletteIdx = 0;
    allProviders.forEach(p => {
      if (!map[p]) {
        map[p] = PALETTE[paletteIdx % PALETTE.length];
        paletteIdx++;
      }
    });
    return map;
  }, [summary]);

  const dashboardFilterParams = useMemo(() => ({
    model: activeFilter?.models,
    provider: activeFilter?.provider,
    client_source: activeSource,
    device_id: activeDevice,
    since: getSinceDate(dateRange),
  }), [activeFilter, activeSource, activeDevice, dateRange, refreshTrigger])

  const applyFilterParams = useCallback((url: URL) => {
    const { model, ...filters } = dashboardFilterParams
    appendModels(url.searchParams, model)
    for (const [key, value] of Object.entries(filters)) {
      if (value) url.searchParams.set(key, value)
    }
    return url
  }, [dashboardFilterParams])

  // Dashboard fetch
  useEffect(() => {
    const controller = new AbortController()
    const requestId = ++dashboardRequestRef.current
    const isCurrentRequest = () => !controller.signal.aborted && dashboardRequestRef.current === requestId
    const sig = { signal: controller.signal }

    async function fetchDashboard() {
      const isInitialDashboardLoad = !dashboardHasLoadedRef.current
      setError(null)
      setDashboardInitialLoading(isInitialDashboardLoad)
      setDashboardRefreshing(!isInitialDashboardLoad)
      try {
        const summaryUrl = applyFilterParams(new URL('/usage/summary', window.location.origin))
        const dailyUrl = applyFilterParams(new URL('/usage/daily', window.location.origin))
        dailyUrl.searchParams.set('tz_offset', getTimezoneOffset(tz))
        dailyUrl.searchParams.set('granularity', rangeGranularity(dateRange))

        const heatmapUrl = new URL('/usage/daily', window.location.origin)
        heatmapUrl.searchParams.set('since', new Date(Date.now() - 730 * 24 * 60 * 60 * 1000).toISOString())
        heatmapUrl.searchParams.set('until', new Date().toISOString())
        heatmapUrl.searchParams.set('granularity', 'day')
        heatmapUrl.searchParams.set('tz_offset', getTimezoneOffset(tz))
        if (activeFilter) {
          appendModels(heatmapUrl.searchParams, activeFilter.models)
          if (activeFilter.provider) heatmapUrl.searchParams.set('provider', activeFilter.provider)
        }
        if (activeSource) heatmapUrl.searchParams.set('client_source', activeSource)
        if (activeDevice) heatmapUrl.searchParams.set('device_id', activeDevice)

        const totalCountUrl = new URL('/usage/count', window.location.origin)

        const responses = await Promise.all([
          fetch(summaryUrl.toString(), sig),
          includeDaily ? fetch(dailyUrl.toString(), sig) : null,
          includeHeatmap ? fetch(heatmapUrl.toString(), sig) : null,
          includeCount ? fetch(totalCountUrl.toString(), sig) : null,
        ])

        if (responses.some(r => r && !r.ok)) throw new Error(t('Failed to fetch dashboard data'))
        const [summaryData, dailyData, heatmapRaw, totalCountData] =
          await Promise.all(responses.map(r => r ? r.json() : null)) as [UsageSummary[], DailyUsage[] | null, DailyUsage[] | null, { total: number } | null]

        if (!isCurrentRequest()) return

        setSummary(summaryData)
        if (!activeFilter) setSelectorSummary(summaryData)
        if (dailyData) setDailyUsage(dailyData)
        if (heatmapRaw) setHeatmapData(heatmapRaw)
        if (totalCountData) setTotalTrackedEvents(totalCountData.total)
        dashboardHasLoadedRef.current = true
      } catch (err) {
        if (!isCurrentRequest()) return
        setError(err instanceof Error ? err.message : t('Unknown error'))
      } finally {
        if (isCurrentRequest()) {
          setDashboardInitialLoading(false)
          setDashboardRefreshing(false)
        }
      }
    }

    void fetchDashboard()
    return () => controller.abort()
  }, [activeFilter, activeSource, activeDevice, dateRange, refreshTrigger, applyFilterParams, setError, tz, includeDaily, includeHeatmap, includeCount])

  // Fetch the selector options without the model filter.
  useEffect(() => {
    if (!activeFilter) return
    const controller = new AbortController()
    const url = new URL('/usage/summary', window.location.origin)
    const since = getSinceDate(dateRange)
    if (since) url.searchParams.set('since', since)
    if (activeSource) url.searchParams.set('client_source', activeSource)
    if (activeDevice) url.searchParams.set('device_id', activeDevice)
    fetch(url.toString(), { signal: controller.signal })
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then(data => { if (!controller.signal.aborted) setSelectorSummary(data) })
      .catch(() => { if (!controller.signal.aborted) setError(t('Failed to fetch dashboard data')) })
    return () => controller.abort()
  }, [dateRange, activeSource, activeDevice, refreshTrigger, !!activeFilter, setError])

  // Sources fetch
  useEffect(() => {
    const controller = new AbortController()
    const since = dashboardFilterParams.since
    const url = new URL('/usage/sources', window.location.origin)
    if (since) url.searchParams.set('since', since)
    fetch(url.toString(), { signal: controller.signal })
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then((data: string[]) => setSources(data))
      .catch(() => {})
    return () => controller.abort()
  }, [dashboardFilterParams.since, refreshTrigger])

  const totals = useMemo(() => {
    const data = summary

    const requests = data.reduce((sum, row) => sum + (row.requests || 0), 0)
    const promptTokens = data.reduce((sum, row) => sum + (row.prompt_tokens || 0), 0)
    const completionTokens = data.reduce((sum, row) => sum + (row.completion_tokens || 0), 0)
    const cachedTokens = data.reduce((sum, row) => sum + (row.cached_tokens || 0), 0)
    const cacheCreationTokens = data.reduce((sum, row) => sum + (row.cache_creation_tokens || 0), 0)
    const totalTokens = data.reduce((sum, row) => sum + (row.total_tokens || 0), 0)
    const totalCost = data.reduce((sum, row) => sum + (row.total_cost_usd || 0), 0)
    const latencySum = data.reduce((sum, row) => sum + (row.latency_sum_ms || 0), 0)

    return {
      requests,
      promptTokens,
      completionTokens,
      cachedTokens,
      cacheCreationTokens,
      totalTokens,
      totalCost,
      avgEffectivePricePerMillion: totalTokens === 0 ? 0 : (totalCost / totalTokens) * 1_000_000,
      avgThroughput: netThroughput(completionTokens, latencySum),
      avgTokensPerRequest: requests === 0 ? 0 : totalTokens / requests
    }
  }, [summary])

  return {
    summary, selectorSummary, dailyUsage, heatmapData, totalTrackedEvents, sources,
    dashboardInitialLoading, dashboardRefreshing,
    providerColors, dashboardFilterParams, totals,
  }
}
