import { appendModels } from '../modelFilter'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type { DailyUsage, DateRangeOption, UsageSummary } from '../types'
import { getSinceDate, rangeGranularity } from '../dateRange.ts'
import { getTimezoneOffset, resolveTimezone, providerColor, netThroughput } from '../utils'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import type { ReadError } from '../components/ReadState'

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
  // A new range or filter is loading: the overview stays mounted, but its
  // totals belong to the old view and must not read as current.
  const [summaryPending, setSummaryPending] = useState(false)
  // A failed summary replaces the overview with Retry; the trend and heatmap
  // reads fail on their own panels. The count only feeds onboarding.
  const [dashboardError, setDashboardError] = useState<string | null>(null)
  const [dailyError, setDailyError] = useState(false)
  // A same-filter re-read keeps the failure (as 'retrying'), so the heatmap's
  // error and its focused Retry stay mounted until the read settles.
  const [heatmapError, setHeatmapError] = useState<ReadError>(false)
  const heatmapRequestedRef = useRef<string | null>(null)
  // The view the daily totals belong to: until it is the current one, the
  // trend panel and the sparklines wait, as the summary does.
  const [dailyView, setDailyView] = useState<string | null>(null)
  // Likewise for the heatmap, whose two-year window ignores the date range.
  const [heatmapViewLoaded, setHeatmapViewLoaded] = useState<string | null>(null)

  const dashboardRequestRef = useRef(0)
  // The view the shown data belongs to: a refresh of the same view keeps it on
  // screen (dimmed); another view starts from a loading state.
  const loadedViewRef = useRef<string | null>(null)
  const view = JSON.stringify([activeFilter, activeSource, activeDevice, dateRange, tz])
  const heatmapView = JSON.stringify([activeFilter, activeSource, activeDevice, tz])

  const providerColors = useMemo(() => Object.fromEntries(
    Array.from(new Set(summary.map(s => s.provider))).map(p => [p, providerColor(p)]),
  ), [summary]);

  const dashboardFilterParams = useMemo(() => ({
    model: activeFilter?.models,
    provider: activeFilter?.provider,
    client_source: activeSource,
    device_id: activeDevice,
    since: getSinceDate(dateRange),
    // Not a query parameter: lets the panels tell a refresh from a new view.
    view,
  }), [activeFilter, activeSource, activeDevice, dateRange, refreshTrigger, view])

  const applyFilterParams = useCallback((url: URL) => {
    const { model, view: _view, ...filters } = dashboardFilterParams
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
      // Only with nothing on screen (first load, or after a failed summary)
      // does the overview give way to a full loading state.
      const isInitialDashboardLoad = loadedViewRef.current === null
      setSummaryPending(!isInitialDashboardLoad && loadedViewRef.current !== view)
      setError(null)
      setDashboardError(null)
      setDailyError(false)
      const heatmapViewChanged = heatmapRequestedRef.current !== heatmapView
      heatmapRequestedRef.current = heatmapView
      setHeatmapError(e => (heatmapViewChanged ? false : e && 'retrying'))
      setDashboardInitialLoading(isInitialDashboardLoad)
      setDashboardRefreshing(!isInitialDashboardLoad)
      try {
        const summaryUrl = applyFilterParams(new URL('/usage/summary', window.location.origin))
        const dailyUrl = applyFilterParams(new URL('/usage/daily', window.location.origin))
        dailyUrl.searchParams.set('tz_offset', getTimezoneOffset(tz))
        dailyUrl.searchParams.set('tz', tz)
        dailyUrl.searchParams.set('granularity', rangeGranularity(dateRange))

        const heatmapUrl = new URL('/usage/daily', window.location.origin)
        heatmapUrl.searchParams.set('since', new Date(Date.now() - 730 * 24 * 60 * 60 * 1000).toISOString())
        heatmapUrl.searchParams.set('until', new Date().toISOString())
        heatmapUrl.searchParams.set('granularity', 'day')
        heatmapUrl.searchParams.set('tz_offset', getTimezoneOffset(tz))
        heatmapUrl.searchParams.set('tz', tz)
        if (activeFilter) {
          appendModels(heatmapUrl.searchParams, activeFilter.models)
          if (activeFilter.provider) heatmapUrl.searchParams.set('provider', activeFilter.provider)
        }
        if (activeSource) heatmapUrl.searchParams.set('client_source', activeSource)
        if (activeDevice) heatmapUrl.searchParams.set('device_id', activeDevice)

        const totalCountUrl = new URL('/usage/count', window.location.origin)

        const read = async (url: URL) => {
          const response = await fetch(url.toString(), sig)
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          return response.json()
        }
        // Each panel applies its own read as it settles: a slow trend, heatmap
        // or count read holds back neither the KPIs nor the other panels, and
        // its failure stays on its own panel.
        const panel = <T,>(include: boolean, url: URL, apply: (data: T) => void, fail: (err: unknown) => void = () => {}) => include
          ? (read(url) as Promise<T>).then(
            data => { if (isCurrentRequest()) apply(data) },
            err => { if (isCurrentRequest()) fail(err) },
          )
          : null
        const summaryRead = read(summaryUrl) as Promise<UsageSummary[]>
        const panelReads = Promise.all([
          panel<DailyUsage[]>(includeDaily, dailyUrl, data => {
            setDailyUsage(data)
            setDailyView(view)
          }, err => {
            console.error('Failed to load usage trend:', err)
            setDailyError(true)
          }),
          panel<DailyUsage[]>(includeHeatmap, heatmapUrl, data => {
            setHeatmapData(data)
            setHeatmapViewLoaded(heatmapView)
            setHeatmapError(false)
          }, err => {
            console.error('Failed to load activity:', err)
            setHeatmapError('failed')
          }),
          panel<{ total: number }>(includeCount, totalCountUrl, data => setTotalTrackedEvents(data.total)),
        ])

        const summaryData = await summaryRead
        if (!isCurrentRequest()) return
        setSummary(summaryData)
        if (!activeFilter) setSelectorSummary(summaryData)
        loadedViewRef.current = view
        setSummaryPending(false)
        setDashboardInitialLoading(false)

        // The refresh ends once every read has settled.
        await panelReads
      } catch (err) {
        if (!isCurrentRequest()) return
        // The raw error may quote the response body: the console gets it, the UI a fixed message.
        console.error('Failed to fetch dashboard data:', err)
        setError(t('Failed to fetch dashboard data'))
        setDashboardError(t('Failed to fetch dashboard data'))
        // Whatever loaded before is now stale: the next read starts from a loading state.
        loadedViewRef.current = null
      } finally {
        if (isCurrentRequest()) {
          setDashboardInitialLoading(false)
          setDashboardRefreshing(false)
          setSummaryPending(false)
        }
      }
    }

    void fetchDashboard()
    return () => controller.abort()
  }, [activeFilter, activeSource, activeDevice, dateRange, refreshTrigger, applyFilterParams, setError, tz, includeDaily, includeHeatmap, includeCount, view, heatmapView])

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
    dashboardInitialLoading, dashboardRefreshing, summaryPending, dashboardError, dailyError, heatmapError,
    dailyPending: includeDaily && dailyView !== view,
    heatmapPending: includeHeatmap && heatmapViewLoaded !== heatmapView,
    providerColors, dashboardFilterParams, totals,
  }
}
