import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildSessionInsights, customBound, fillGaps, getSinceDate, netThroughput, resolveTimezone } from '../src/utils'
import { overrideBody, pricingSourceOrder } from '../src/hooks/usePricingData'
import { buildTimelineData } from '../src/components/RequestTimeline'
import type { DailyUsage, SessionSummary } from '../src/types'

vi.mock('../src/i18n/index.ts', () => ({ t: (value: string) => value, useLang: () => 'en' }))

afterEach(() => vi.useRealTimers())

describe('date ranges and usage gaps', () => {
  it('calculates relative API bounds and leaves unbounded ranges open', () => {
    vi.useFakeTimers().setSystemTime(new Date('2025-04-15T12:00:00Z'))
    expect(Date.parse(getSinceDate('24h')!)).toBe(Date.parse('2025-04-14T12:00:00Z'))
    expect(Date.parse(getSinceDate('7d')!)).toBe(Date.parse('2025-04-08T12:00:00Z'))
    expect(Date.parse(getSinceDate('30d')!)).toBe(Date.parse('2025-03-16T12:00:00Z'))
    expect(getSinceDate('all')).toBeNull()
    expect(getSinceDate('custom')).toBeNull()
    expect(Date.parse(customBound('2025-04-15T12:30'))).toBe(new Date(2025, 3, 15, 12, 30).getTime())
    expect(customBound('')).toBe('')
  })
  it('preserves existing rows and fills missing daily and hourly buckets in the requested timezone', () => {
    vi.useFakeTimers().setSystemTime(new Date('2025-01-02T01:30:00Z'))
    const existing = { period: '2025-01-01', requests: 7, total_tokens: 42 } as DailyUsage
    const daily = fillGaps([existing], 'day', 3, 'UTC')
    expect(daily.map(row => row.period)).toEqual(['2024-12-31', '2025-01-01', '2025-01-02'])
    expect(daily[1]).toBe(existing)
    expect(daily[0]).toMatchObject({ requests: 0, total_tokens: 0, total_cost_usd: 0, failed_requests: 0 })
    expect(fillGaps([], 'hour', 3, 'America/New_York').map(row => row.period)).toEqual(['2025-01-01 18:00', '2025-01-01 19:00', '2025-01-01 20:00'])
    expect(fillGaps([], 'day', 0, 'UTC')).toEqual([])
  })
  it('falls back for invalid timezones and handles zero latency', () => {
    expect(resolveTimezone('invalid/timezone')).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone)
    expect(resolveTimezone('UTC')).toBe('UTC')
    expect(netThroughput(200, 500)).toBe(400)
    expect(netThroughput(200, 0)).toBe(0)
    expect(netThroughput(null, 500)).toBe(0)
  })
})

describe('pricing overrides', () => {
  const draft = { input: ' 1.5 ', output: '2', cacheRead: '', cacheWrite: '0.25' }
  it('converts valid rates and preserves optional cache rates as null', () => {
    expect(overrideBody(draft)).toEqual({ input: 1.5, output: 2, cache_read: null, cache_write: 0.25 })
  })
  it('rejects missing, negative and non-finite rates', () => {
    expect(overrideBody({ ...draft, input: ' ' })).toBe('input')
    expect(overrideBody({ ...draft, output: '-1' })).toBe('output')
    expect(overrideBody({ ...draft, cacheRead: 'Infinity' })).toBe('cacheRead')
    expect(overrideBody({ ...draft, cacheWrite: 'oops' })).toBe('cacheWrite')
  })
  it('prioritizes user and server rates and deduplicates discovered sources', () => {
    expect(pricingSourceOrder(['z', 'litellm', 'user', 'z', 'a'])).toEqual(['user', 'server', 'litellm', 'a', 'z'])
    expect(pricingSourceOrder()).toEqual(['user', 'server', 'openrouter', 'litellm'])
  })
})

describe('request timeline data', () => {
  it('clamps first-token time and separates untimed tools', () => {
    const result = buildTimelineData({ latencyMs: 100, ttftMs: 200, toolCalls: [{ tool_name: 'a', tool_use_id: '1' }] })
    expect(result.rows.find(row => row.type === 'ttft')?.durationMs).toBe(100)
    expect(result.untimed).toHaveLength(1)
    expect(result.totalMs).toBe(100)
  })
  it('groups overlapping timestamped tools and measures the full span', () => {
    const result = buildTimelineData({ latencyMs: 100, toolCalls: [
      { tool_name: 'a', tool_use_id: '1', duration_ms: 80, ts: 1000 },
      { tool_name: 'b', tool_use_id: '2', duration_ms: 50, ts: 1020 },
      { tool_name: 'c', tool_use_id: '3', duration_ms: 20, ts: 1100 },
    ] })
    expect(result.rows.filter(row => row.type === 'parallel_group')).toHaveLength(1)
    expect(result.rows.filter(row => row.type === 'tool')).toHaveLength(1)
    expect(result.totalMs).toBe(220)
    expect(result.untimed).toEqual([])
  })
})


describe('session insights', () => {
  it('selects sessions by cost, latency, tokens, cache and failures without reordering input', () => {
    const base: SessionSummary = {
      session_id: 'a', client_source: 'test', model: 'model', started: 0, ended: 0, duration_s: 0,
      request_count: 1, total_tokens: 10, prompt_tokens: 10, completion_tokens: 0, cached_tokens: 0,
      cache_creation_tokens: 0, total_cost_usd: 1, avg_latency_ms: 1, latency_sum_ms: 1, avg_ttft_ms: 0,
      successful_requests: 1, failed_requests: 0, status_429: null, status_4xx: null, status_5xx: null,
      status_unknown: null, tool_calls_json: null, evaluation: null,
    }
    const other = { ...base, session_id: 'b', total_cost_usd: 2, avg_latency_ms: 5, total_tokens: 20, cached_tokens: 5, failed_requests: 1 }
    const sessions = [base, other]
    expect(buildSessionInsights([])).toEqual([])
    expect(buildSessionInsights(sessions).map(insight => insight.session)).toEqual([other, other, other, other, other])
    expect(sessions).toEqual([base, other])
    expect(buildSessionInsights([base]).map(insight => insight.session)).toEqual([base, base, base])
  })
})
