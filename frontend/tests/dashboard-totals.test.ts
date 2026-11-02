// @vitest-environment jsdom
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useDashboardData } from '../src/hooks/useDashboardData'
import type { ActiveFilter } from '../src/types'

const context = vi.hoisted(() => ({ refreshTrigger: 0, setError: () => {}, activeFilter: null as ActiveFilter, activeSource: null, activeDevice: null, dateRange: '5h', timezone: 'UTC' }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => context }))

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); context.refreshTrigger = 0; context.dateRange = '5h'; context.activeFilter = null })

it('sends each selected model as a separate summary and daily query parameter', async () => {
  const models = ['gpt-4.1', 'claude-sonnet-4']
  context.activeFilter = { models }
  const fetchMock = vi.fn(async (_url: string) => ({ ok: true, json: async () => [] }))
  vi.stubGlobal('fetch', fetchMock)
  const { result } = renderHook(() => useDashboardData({ includeHeatmap: false, includeCount: false }))
  await waitFor(() => expect(result.current.dashboardInitialLoading).toBe(false))

  const urls = fetchMock.mock.calls.map(([url]) => new URL(url))
  for (const path of ['/usage/summary', '/usage/daily']) {
    // The selector also fetches an unfiltered summary for its available options.
    const filteredRequests = urls.filter(url => url.pathname === path && url.searchParams.has('model'))
    expect(filteredRequests).toHaveLength(1)
    expect(filteredRequests[0].searchParams.getAll('model')).toEqual(models)
  }
})

it('uses the shared summary for five-hour totals', async () => {
  const row = { provider: 'test', requests: 2, prompt_tokens: 100, completion_tokens: 50, cached_tokens: 40, cache_creation_tokens: 10, total_tokens: 150, total_cost_usd: 2, latency_sum_ms: 1000 }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.includes('/usage/summary') ? [row] : url.includes('/usage/daily') ? [{ ...row, total_tokens: 999, requests: 9 }] : [] })))
  const { result } = renderHook(() => useDashboardData({ includeHeatmap: false, includeCount: false }))
  await waitFor(() => expect(result.current.dashboardInitialLoading).toBe(false))
  expect(result.current.totals).toMatchObject({ requests: 2, promptTokens: 100, completionTokens: 50, cachedTokens: 40, cacheCreationTokens: 10, totalTokens: 150, totalCost: 2, avgThroughput: 50 })
})


it('shares one advancing-clock window with dashboard requests and refreshes it', async () => {
  context.dateRange = '24h'
  let now = Date.parse('2026-05-07T10:15:00Z')
  vi.spyOn(Date, 'now').mockImplementation(() => now++)
  const fetchMock = vi.fn(async (_url: string) => ({ ok: true, json: async () => [] }))
  vi.stubGlobal('fetch', fetchMock)
  const { result, rerender } = renderHook(() => useDashboardData({ includeHeatmap: false, includeCount: false }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3))
  const checkWindow = (start: number) => {
    const urls = fetchMock.mock.calls.slice(start).map(([url]) => new URL(url as string))
    expect(urls.map(url => url.pathname).sort()).toEqual(['/usage/daily', '/usage/sources', '/usage/summary'])
    expect(urls.map(url => url.searchParams.get('since'))).toEqual(Array(3).fill(result.current.dashboardFilterParams.since))
  }
  checkWindow(0)
  const firstSince = result.current.dashboardFilterParams.since
  now += 60_000
  context.refreshTrigger++
  rerender()
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6))
  checkWindow(3)
  expect(result.current.dashboardFilterParams.since).not.toBe(firstSince)
})
