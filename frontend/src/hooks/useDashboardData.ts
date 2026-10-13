import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type { DailyUsage, UsageSummary } from '../types'
import { getSinceDate, getTimezoneOffset, resolveTimezone, FIXED_PROVIDER_COLORS, PALETTE, netThroughput } from '../utils'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'

export function useDashboardData() {
  const { refreshTrigger, setError, activeFilter, activeSource, activeDevice, dateRange, customSince, customUntil, timezone } = useApp()
  const tz = resolveTimezone(timezone)

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

  const applyFilterParams = useCallback((url: URL) => {
    const since = dateRange === 'custom' ? customSince : getSinceDate(dateRange)
    const until = dateRange === 'custom' ? customUntil : null

    if (activeFilter) {
      if (activeFilter.provider) url.searchParams.set('provider', activeFilter.provider)
      if (activeFilter.model) url.searchParams.set('model', activeFilter.model)
      if (activeFilter.only_failed) url.searchParams.set('only_failed', 'true')
      if (activeFilter.status_429) url.searchParams.set('status_429', 'true')
      if (activeFilter.status_4xx) url.searchParams.set('status_4xx', 'true')
      if (activeFilter.status_5xx) url.searchParams.set('status_5xx', 'true')
    }
    if (activeSource) url.searchParams.set('client_source', activeSource)
    if (activeDevice) url.searchParams.set('device_id', activeDevice)
    if (since) url.searchParams.set('since', since)
    if (until) url.searchParams.set('until', until)
    return url
  }, [dateRange, customSince, customUntil, activeFilter, activeSource, activeDevice])

  const dashboardFilterParams = useMemo(() => ({
    provider: activeFilter?.provider,
    model: activeFilter?.model,
    client_source: activeSource,
    device_id: activeDevice,
    since: dateRange === 'custom' ? customSince : getSinceDate(dateRange),
    until: dateRange === 'custom' ? customUntil : null,
    only_failed: activeFilter?.only_failed,
    status_429: activeFilter?.status_429,
    status_4xx: activeFilter?.status_4xx,
    status_5xx: activeFilter?.status_5xx
  }), [activeFilter, activeSource, activeDevice, dateRange, customSince, customUntil])

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
        if (dateRange === '24h') dailyUrl.searchParams.set('granularity', 'hour')

        const heatmapUrl = new URL('/usage/daily', window.location.origin)
        heatmapUrl.searchParams.set('since', new Date(Date.now() - 730 * 24 * 60 * 60 * 1000).toISOString())
        heatmapUrl.searchParams.set('until', new Date().toISOString())
        heatmapUrl.searchParams.set('granularity', 'day')
        heatmapUrl.searchParams.set('tz_offset', getTimezoneOffset(tz))
        if (activeFilter) {
          if (activeFilter.provider) heatmapUrl.searchParams.set('provider', activeFilter.provider)
          if (activeFilter.model) heatmapUrl.searchParams.set('model', activeFilter.model)
          if (activeFilter.only_failed) heatmapUrl.searchParams.set('only_failed', 'true')
          if (activeFilter.status_429) heatmapUrl.searchParams.set('status_429', 'true')
          if (activeFilter.status_4xx) heatmapUrl.searchParams.set('status_4xx', 'true')
          if (activeFilter.status_5xx) heatmapUrl.searchParams.set('status_5xx', 'true')
        }
        if (activeSource) heatmapUrl.searchParams.set('client_source', activeSource)
        if (activeDevice) heatmapUrl.searchParams.set('device_id', activeDevice)

        const totalCountUrl = new URL('/usage/count', window.location.origin)

        const responses = await Promise.all([
          fetch(summaryUrl.toString(), sig),
          fetch(dailyUrl.toString(), sig),
          fetch(heatmapUrl.toString(), sig),
          fetch(totalCountUrl.toString(), sig),
        ])

        if (responses.some(r => !r.ok)) throw new Error(t('Failed to fetch dashboard data'))
        const [summaryData, dailyData, heatmapRaw, totalCountData] =
          await Promise.all(responses.map(r => r.json())) as [UsageSummary[], DailyUsage[], DailyUsage[], { total: number }]

        if (!isCurrentRequest()) return

        setSummary(summaryData)
        setDailyUsage(dailyData)
        setHeatmapData(heatmapRaw)
        setTotalTrackedEvents(totalCountData.total)
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
  }, [activeFilter, activeSource, activeDevice, dateRange, customSince, customUntil, refreshTrigger, applyFilterParams, setError, tz])

  // Sources fetch
  useEffect(() => {
    const controller = new AbortController()
    const since = dateRange === 'custom' ? customSince : getSinceDate(dateRange)
    const until = dateRange === 'custom' ? customUntil : null
    const url = new URL('/usage/sources', window.location.origin)
    if (since) url.searchParams.set('since', since)
    if (until) url.searchParams.set('until', until)
    fetch(url.toString(), { signal: controller.signal })
      .then(r => r.ok ? r.json() : Promise.reject(r))
      .then((data: string[]) => setSources(data))
      .catch(() => {})
    return () => controller.abort()
  }, [dateRange, customSince, customUntil, refreshTrigger])

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
    const toolDurationSum = data.reduce((sum, row) => sum + (row.tool_duration_sum_ms || 0), 0)

    return {
      requests,
      promptTokens,
      completionTokens,
      cachedTokens,
      cacheCreationTokens,
      totalTokens,
      totalCost,
      avgEffectivePrice: requests === 0 ? 0 : totalCost / requests,
      avgEffectivePricePerMillion: totalTokens === 0 ? 0 : (totalCost / totalTokens) * 1_000_000,
      avgThroughput: netThroughput(completionTokens, latencySum, toolDurationSum),
      avgTokensPerRequest: requests === 0 ? 0 : totalTokens / requests
    }
  }, [summary])

  return {
    summary, dailyUsage, heatmapData, totalTrackedEvents, sources,
    dashboardInitialLoading, dashboardRefreshing,
    providerColors, dashboardFilterParams, totals,
  }
}
