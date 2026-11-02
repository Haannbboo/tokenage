import { appendModels } from '../modelFilter'
import { useState, useMemo, useEffect } from 'react'
import type { Theme } from '../theme'
import type { UsageSummary } from '../types'
import { getModelColor, getModelBadgeBackgroundColor, getModelTextColor } from '../model-badge'
import { formatModelName, formatProviderName, getModelIcon, getProviderIcon, getProviderBadgeBg, getProviderBadgeText, getSourceBadgeBg, getSourceBadgeText, getSourceIcon, PALETTE, netThroughput, getTimezoneOffset, resolveTimezone } from '../utils'
import { useApp } from '../contexts/AppContext'
import { HorizontalBarChart } from './HorizontalBarChart'
import type { BarItem, Metric } from './HorizontalBarChart'
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

const providerColors: Record<string, string> = {
  'anthropic': '#cc7c5e',
  'google': '#528af2',
  'openai': '#94a3b8',
  'xiaomi': '#dcc496',
}

export function TopUsageChart({
  summary,
  theme,
  filterParams = {},
}: {
  summary: UsageSummary[]
  theme: Theme
  filterParams?: {
    provider?: string
    model?: string[] | null
    client_source?: string | null
    device_id?: string | null
    since?: string | null
  }
}) {
  const [dimension, setDimension] = useState<Dimension>('model')
  const [metric, setMetric] = useState<Metric>('tokens')
  const [sourceSummary, setSourceSummary] = useState<SourceSummaryRow[]>([])
  const [deviceRows, setDeviceRows] = useState<DeviceDailyRow[]>([])
  const tzOffset = getTimezoneOffset(resolveTimezone(useApp().timezone))

  // Fetch source summary data when source dimension is selected
  useEffect(() => {
    if (dimension !== 'source') return
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
        if (res.ok) {
          setSourceSummary(await res.json())
        }
      } catch {
        // Ignore abort errors
      }
    }
    fetchSourceSummary()
    return () => controller.abort()
  }, [dimension, filterParams])

  useEffect(() => {
    if (dimension !== 'device') return
    const controller = new AbortController()
    setDeviceRows([])
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
        if (res.ok) {
          const rows = await res.json()
          if (!controller.signal.aborted) setDeviceRows(rows)
        }
      } catch {
        // Ignore abort errors
      }
    }
    fetchDeviceRows()
    return () => controller.abort()
  }, [dimension, filterParams, tzOffset])

  const items: BarItem[] = useMemo(() => {
    if (dimension === 'model') {
      const map = new Map<string, { tokens: number; completion: number; prompt: number; cached: number; cacheCreation: number; latency: number; cost: number; priceWeight: number; priceTokens: number; successful: number; total: number }>()
      for (const s of summary) {
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
        color: getModelColor(model, theme),
        badgeBg: getModelBadgeBackgroundColor(model, theme),
        badgeText: getModelTextColor(model, theme),
      }))
    }

    if (dimension === 'provider') {
      const map = new Map<string, { tokens: number; completion: number; prompt: number; cached: number; cacheCreation: number; latency: number; cost: number; successful: number; total: number }>()
      for (const s of summary) {
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
      return Array.from(map.entries()).map(([provider, v], i) => ({
        name: formatProviderName(provider),
        icon: getProviderIcon(provider, theme),
        tokens: v.tokens,
        promptTokens: v.prompt,
        completionTokens: v.completion,
        cachedTokens: v.cached,
        cost: v.cost,
        throughput: netThroughput(v.completion, v.latency),
        successRate: v.total > 0 ? (v.successful / v.total) * 100 : 100,
        cacheHitRate: (v.prompt + v.cacheCreation) > 0 ? (v.cached / (v.prompt + v.cacheCreation)) * 100 : 0,
        color: providerColors[provider.toLowerCase()] || PALETTE[i % PALETTE.length],
        badgeBg: getProviderBadgeBg(provider, theme),
        badgeText: getProviderBadgeText(provider, theme),
      }))
    }

    if (dimension === 'device') {
      const map = new Map<string, { name: string; tokens: number; completion: number; prompt: number; cached: number; cacheCreation: number; latency: number; cost: number; successful: number; total: number }>()
      for (const r of deviceRows) {
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
      return Array.from(map.values()).map((v, i) => ({
        name: v.name,
        icon: null,
        tokens: v.tokens,
        promptTokens: v.prompt,
        completionTokens: v.completion,
        cachedTokens: v.cached,
        cost: v.cost,
        throughput: netThroughput(v.completion, v.latency),
        pricePerMillion: v.tokens > 0 ? (v.cost / v.tokens) * 1_000_000 : null,
        successRate: v.total > 0 ? (v.successful / v.total) * 100 : 100,
        cacheHitRate: (v.prompt + v.cacheCreation) > 0 ? (v.cached / (v.prompt + v.cacheCreation)) * 100 : 0,
        color: PALETTE[i % PALETTE.length],
        badgeBg: getSourceBadgeBg(v.name),
        badgeText: getSourceBadgeText(v.name),
      }))
    }

    // source dimension
    return sourceSummary.map((row, i) => {
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
        tokens,
        promptTokens: prompt,
        completionTokens: row.completion_tokens ?? 0,
        cachedTokens: cached,
        cost,
        throughput: row.avg_throughput ?? 0,
        pricePerMillion: tokens > 0 ? (cost / tokens) * 1_000_000 : null,
        successRate: total > 0 ? ((row.successful_requests ?? 0) / total) * 100 : 100,
        cacheHitRate: (prompt + cacheCreation) > 0 ? (cached / (prompt + cacheCreation)) * 100 : 0,
        color: PALETTE[i % PALETTE.length],
        badgeBg: getSourceBadgeBg(name),
        badgeText: getSourceBadgeText(name),
      }
    })
  }, [summary, dimension, theme, sourceSummary, deviceRows])

  const dimensionLabel = dimension === 'model' ? t('Models') : dimension === 'provider' ? t('Providers') : dimension === 'device' ? t('Devices') : t('Sources')

  const chart = (
    <HorizontalBarChart
      title={`${t('Top')} ${dimensionLabel}`}
      icon="📊"
      items={items}
      metric={metric}
    >
      <div style={{
        padding: '8px 12px',
        borderTop: '1px solid var(--border-color)',
        display: 'flex',
        flexWrap: 'wrap',
        justifyContent: 'space-between',
        alignItems: 'center',
        gap: '8px',
      }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px', background: 'var(--tab-toggle-bg)', borderRadius: '6px', padding: '2px' }}>
          {(['model', 'provider', 'source', 'device'] as Dimension[]).map(d => (
            <button
              key={d}
              className={`tab-toggle-btn ${dimension === d ? 'active' : ''}`}
              onClick={() => setDimension(d)}
            >
              {d === 'model' ? t('Models') : d === 'provider' ? t('Providers') : d === 'device' ? t('Devices') : t('Sources')}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px', background: 'var(--tab-toggle-bg)', borderRadius: '6px', padding: '2px' }}>
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
