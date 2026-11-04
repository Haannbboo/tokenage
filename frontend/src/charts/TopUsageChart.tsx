import { appendModels } from '../modelFilter'
import { useState, useMemo, useEffect, useRef } from 'react'
import type { Theme } from '../theme'
import type { UsageSummary } from '../types'
import { formatModelName, formatProviderName, getModelIcon, getProviderIcon, getSourceIcon, modelColor, providerColor, sourceColor, nameColor, netThroughput, getTimezoneOffset, resolveTimezone } from '../utils'
import { useApp } from '../contexts/AppContext'
import { HorizontalBarChart } from './HorizontalBarChart'
import type { BarItem, Metric } from './HorizontalBarChart'
import { ReadState, useReadRetry } from '../components/ReadState'
import { t } from '../i18n/index.ts'

type Dimension = 'model' | 'provider' | 'source' | 'device'

// One row per (device, day) from /usage/daily-by-dimension?dimension=device.
// dimension is null for usage that cannot be attributed (shown as Unknown).
type DeviceDailyRow = {
  dimension: string | null
  dimension_name: string | null
  requests: number | null
  prompt_tokens: number | null
  cached_tokens: number | null
  cache_creation_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  total_cost_usd: number | null
  latency_sum_ms: number | null
  successful_requests: number | null
  failed_requests: number | null
}

type SourceSummaryRow = {
  client_source: string | null
  total_tokens: number | null
  prompt_tokens: number | null
  cached_tokens: number | null
  cache_creation_tokens: number | null
  completion_tokens: number | null
  latency_sum_ms: number | null
  avg_throughput: number | null
  total_cost_usd: number | null
  successful_requests: number | null
  failed_requests: number | null
}

// A stable default: a fresh {} per render would re-run the fetch effect forever.
const NO_FILTERS = {}

export function TopUsageChart({
  summary,
  theme,
  filterParams = NO_FILTERS,
  summaryPending = false,
}: {
  summary: UsageSummary[]
  /** `summary` still belongs to the previous view: the model and provider lists wait for it. */
  summaryPending?: boolean
  theme: Theme
  filterParams?: {
    provider?: string
    model?: string[] | null
    client_source?: string | null
    device_id?: string | null
    since?: string | null
    view?: string
  }
}) {
  const [dimension, setDimension] = useState<Dimension>('model')
  const [metric, setMetric] = useState<Metric>('tokens')
  // null while loading, so another range's rows never show as current.
  const [sourceSummary, setSourceSummary] = useState<SourceSummaryRow[] | null>(null)
  const [deviceRows, setDeviceRows] = useState<DeviceDailyRow[] | null>(null)
  // Shared by the source and device reads: a dimension change clears it.
  const { error: readError, setError: setReadError, attempt, retry } = useReadRetry()
  // A refresh of the same view keeps the rows (dimmed by the page); another view clears them.
  const view = filterParams.view ?? filterParams
  const sourceViewRef = useRef<unknown>(view)
  const deviceViewRef = useRef<unknown>(view)
  const tzOffset = getTimezoneOffset(resolveTimezone(useApp().timezone))

  // Fetch source summary data when source dimension is selected
  useEffect(() => {
    if (dimension !== 'source') return
    if (sourceViewRef.current !== view) { sourceViewRef.current = view; setSourceSummary(null); setReadError(false) }
    const controller = new AbortController()
    async function fetchSourceSummary() {
      try {
        const url = new URL('/usage/by-source', window.location.origin)
        if (filterParams.provider) url.searchParams.set('provider', filterParams.provider)
        appendModels(url.searchParams, filterParams.model)
        if (filterParams.client_source) url.searchParams.set('client_source', filterParams.client_source)
        if (filterParams.device_id) url.searchParams.set('device_id', filterParams.device_id)
        if (filterParams.since) url.searchParams.set('since', filterParams.since)

        const res = await fetch(url.toString(), { signal: controller.signal })
        if (!res.ok) throw new Error(String(res.status))
        const rows = await res.json()
        if (!controller.signal.aborted) { setSourceSummary(rows); setReadError(false) }
      } catch {
        if (!controller.signal.aborted) setReadError('failed')
      }
    }
    fetchSourceSummary()
    return () => controller.abort()
  }, [dimension, filterParams, attempt, view, setReadError])

  useEffect(() => {
    if (dimension !== 'device') return
    if (deviceViewRef.current !== view) { deviceViewRef.current = view; setDeviceRows(null); setReadError(false) }
    const controller = new AbortController()
    async function fetchDeviceRows() {
      try {
        const url = new URL('/usage/daily-by-dimension', window.location.origin)
        url.searchParams.set('dimension', 'device')
        if (filterParams.provider) url.searchParams.set('provider', filterParams.provider)
        appendModels(url.searchParams, filterParams.model)
        if (filterParams.client_source) url.searchParams.set('client_source', filterParams.client_source)
        if (filterParams.device_id) url.searchParams.set('device_id', filterParams.device_id)
        if (filterParams.since) url.searchParams.set('since', filterParams.since)
        url.searchParams.set('tz_offset', tzOffset)

        const res = await fetch(url.toString(), { signal: controller.signal })
        if (!res.ok) throw new Error(String(res.status))
        const rows = await res.json()
        if (!controller.signal.aborted) { setDeviceRows(rows); setReadError(false) }
      } catch {
        if (!controller.signal.aborted) setReadError('failed')
      }
    }
    fetchDeviceRows()
    return () => controller.abort()
  }, [dimension, filterParams, tzOffset, attempt, view, setReadError])

  const items: BarItem[] = useMemo(() => {
    if (dimension === 'model') {
      const map = new Map<string, { tokens: number; completion: number; prompt: number; cached: number; cacheCreation: number; latency: number; cost: number; priceWeight: number; priceTokens: number; successful: number; total: number }>()
      for (const s of summaryPending ? [] : summary) {
        const existing = map.get(s.model) || { tokens: 0, completion: 0, prompt: 0, cached: 0, cacheCreation: 0, latency: 0, cost: 0, priceWeight: 0, priceTokens: 0, successful: 0, total: 0 }
        const tokens = s.total_tokens ?? 0
        existing.tokens += tokens
        existing.completion += s.completion_tokens ?? 0
        existing.prompt += s.prompt_tokens ?? 0
        existing.cached += s.cached_tokens ?? 0
        existing.cacheCreation += s.cache_creation_tokens ?? 0
        existing.latency += s.latency_sum_ms ?? 0
        existing.cost += s.total_cost_usd ?? 0
        existing.successful += s.successful_requests ?? 0
        existing.total += (s.successful_requests ?? 0) + (s.failed_requests ?? 0)
        if (s.avg_effective_price_per_million_usd != null) {
          existing.priceWeight += s.avg_effective_price_per_million_usd * tokens
          existing.priceTokens += tokens
        }
        map.set(s.model, existing)
      }
      return Array.from(map.entries()).map(([model, v]) => ({
        name: formatModelName(model),
        id: model,
        icon: getModelIcon(model, theme),
        color: modelColor(model),
        tokens: v.tokens,
        promptTokens: v.prompt,
        completionTokens: v.completion,
        cachedTokens: v.cached,
        cost: v.cost,
        throughput: netThroughput(v.completion, v.latency),
        pricePerMillion: v.priceTokens > 0
          ? v.priceWeight / v.priceTokens
          : v.tokens > 0 ? (v.cost / v.tokens) * 1_000_000 : null,
        successRate: v.total > 0 ? (v.successful / v.total) * 100 : 100,
        cacheHitRate: (v.prompt + v.cacheCreation) > 0 ? (v.cached / (v.prompt + v.cacheCreation)) * 100 : 0,
        cacheWriteTokens: v.cacheCreation,
        successful: v.successful,
        failed: v.total - v.successful,
      }))
    }

    if (dimension === 'provider') {
      const map = new Map<string, { tokens: number; completion: number; prompt: number; cached: number; cacheCreation: number; latency: number; cost: number; successful: number; total: number }>()
      for (const s of summaryPending ? [] : summary) {
        const existing = map.get(s.provider) || { tokens: 0, completion: 0, prompt: 0, cached: 0, cacheCreation: 0, latency: 0, cost: 0, successful: 0, total: 0 }
        existing.tokens += s.total_tokens ?? 0
        existing.completion += s.completion_tokens ?? 0
        existing.prompt += s.prompt_tokens ?? 0
        existing.cached += s.cached_tokens ?? 0
        existing.cacheCreation += s.cache_creation_tokens ?? 0
        existing.latency += s.latency_sum_ms ?? 0
        existing.cost += s.total_cost_usd ?? 0
        existing.successful += s.successful_requests ?? 0
        existing.total += (s.successful_requests ?? 0) + (s.failed_requests ?? 0)
        map.set(s.provider, existing)
      }
      return Array.from(map.entries()).map(([provider, v]) => ({
        name: formatProviderName(provider),
        icon: getProviderIcon(provider, theme),
        color: providerColor(provider),
        tokens: v.tokens,
        promptTokens: v.prompt,
        completionTokens: v.completion,
        cachedTokens: v.cached,
        cost: v.cost,
        throughput: netThroughput(v.completion, v.latency),
        successRate: v.total > 0 ? (v.successful / v.total) * 100 : 100,
        cacheHitRate: (v.prompt + v.cacheCreation) > 0 ? (v.cached / (v.prompt + v.cacheCreation)) * 100 : 0,
        cacheWriteTokens: v.cacheCreation,
        successful: v.successful,
        failed: v.total - v.successful,
      }))
    }

    if (dimension === 'device') {
      const map = new Map<string, { name: string; tokens: number; completion: number; prompt: number; cached: number; cacheCreation: number; latency: number; cost: number; successful: number; total: number }>()
      for (const r of deviceRows ?? []) {
        const key = r.dimension ?? 'unknown'
        const existing = map.get(key) || { name: r.dimension_name ?? t('Unknown'), tokens: 0, completion: 0, prompt: 0, cached: 0, cacheCreation: 0, latency: 0, cost: 0, successful: 0, total: 0 }
        existing.tokens += r.total_tokens ?? 0
        existing.completion += r.completion_tokens ?? 0
        existing.prompt += r.prompt_tokens ?? 0
        existing.cached += r.cached_tokens ?? 0
        existing.cacheCreation += r.cache_creation_tokens ?? 0
        existing.latency += r.latency_sum_ms ?? 0
        existing.cost += r.total_cost_usd ?? 0
        existing.successful += r.successful_requests ?? 0
        existing.total += (r.successful_requests ?? 0) + (r.failed_requests ?? 0)
        map.set(key, existing)
      }
      return Array.from(map.values()).map(v => ({
        name: v.name,
        icon: null,
        color: nameColor(v.name),
        tokens: v.tokens,
        promptTokens: v.prompt,
        completionTokens: v.completion,
        cachedTokens: v.cached,
        cost: v.cost,
        throughput: netThroughput(v.completion, v.latency),
        pricePerMillion: v.tokens > 0 ? (v.cost / v.tokens) * 1_000_000 : null,
        successRate: v.total > 0 ? (v.successful / v.total) * 100 : 100,
        cacheHitRate: (v.prompt + v.cacheCreation) > 0 ? (v.cached / (v.prompt + v.cacheCreation)) * 100 : 0,
        cacheWriteTokens: v.cacheCreation,
        successful: v.successful,
        failed: v.total - v.successful,
      }))
    }

    // source dimension
    return (sourceSummary ?? []).map(row => {
      const name = row.client_source || 'unknown'
      const tokens = row.total_tokens ?? 0
      const cost = row.total_cost_usd ?? 0
      const total = (row.successful_requests ?? 0) + (row.failed_requests ?? 0)
      const prompt = row.prompt_tokens ?? 0
      const cached = row.cached_tokens ?? 0
      const cacheCreation = row.cache_creation_tokens ?? 0
      return {
        name,
        icon: getSourceIcon(name),
        color: sourceColor(name),
        tokens,
        promptTokens: prompt,
        completionTokens: row.completion_tokens ?? 0,
        cachedTokens: cached,
        cost,
        throughput: row.avg_throughput ?? 0,
        pricePerMillion: tokens > 0 ? (cost / tokens) * 1_000_000 : null,
        successRate: total > 0 ? ((row.successful_requests ?? 0) / total) * 100 : 100,
        cacheHitRate: (prompt + cacheCreation) > 0 ? (cached / (prompt + cacheCreation)) * 100 : 0,
        cacheWriteTokens: cacheCreation,
        successful: row.successful_requests ?? 0,
        failed: row.failed_requests ?? 0,
      }
    })
  }, [summary, summaryPending, dimension, theme, sourceSummary, deviceRows])

  const dimensionLabel = dimension === 'model' ? t('Models') : dimension === 'provider' ? t('Providers') : dimension === 'device' ? t('Devices') : t('Sources')

  const chart = (
    <HorizontalBarChart
      title={`${t('Top')} ${dimensionLabel}`}
      items={items}
      mono={dimension === 'model'}
      metric={metric}
      status={readError || (summaryPending && (dimension === 'model' || dimension === 'provider')) || (dimension === 'source' && sourceSummary === null) || (dimension === 'device' && deviceRows === null)
        ? <ReadState error={readError === 'failed' ? t('Failed to fetch dashboard data') : null} onRetry={retry} />
        : undefined}
    >
      <div className="top-list-controls">
        <div className="tab-toggle compact">
          {(['model', 'provider', 'source', 'device'] as Dimension[]).map(d => (
            <button
              key={d}
              className={`tab-toggle-btn ${dimension === d ? 'active' : ''}`}
              onClick={() => { if (d !== dimension) { setDimension(d); setReadError(false) } }}
            >
              {d === 'model' ? t('Models') : d === 'provider' ? t('Providers') : d === 'device' ? t('Devices') : t('Sources')}
            </button>
          ))}
        </div>
        <div className="tab-toggle compact">
          {(['tokens', 'cost', 'throughput', 'cacheHitRate', 'successRate'] as Metric[]).map(m => (
            <button
              key={m}
              className={`tab-toggle-btn ${metric === m ? 'active' : ''}`}
              onClick={() => setMetric(m)}
            >
              {m === 'tokens' ? t('Tokens') : m === 'cost' ? t('Cost') : m === 'throughput' ? t('Speed') : m === 'successRate' ? t('Success') : t('Cache')}
            </button>
          ))}
        </div>
      </div>
    </HorizontalBarChart>
  )

  return chart
}
