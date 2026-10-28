// @vitest-environment jsdom
import { createElement, StrictMode } from 'react'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UsageRow } from '../src/types'
import { useLogsData } from '../src/hooks/useLogsData'
import { LogsPage } from '../src/pages/LogsPage'

const app = vi.hoisted(() => ({
  refreshTrigger: 0, setError: vi.fn(), showToast: vi.fn(), requestUsageRefresh: vi.fn(),
  activeFilter: null, activeSource: null as string | null, activeDevice: null,
  dateRange: 'all', timezone: 'UTC', setActiveFilter: vi.fn(), setActiveSource: vi.fn(),
  setActiveDevice: vi.fn(), setDateRange: vi.fn(),
}))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => app }))
vi.mock('../src/hooks/useDashboardData', () => ({ useDashboardData: () => ({ summary: [], sources: [], providerColors: {} }) }))
vi.mock('../src/hooks/useSessionSelectorData', () => ({ useSessionSelectorData: () => ({ sessions: [] }) }))
vi.mock('../src/components/DeviceSelect', () => ({ DeviceSelect: () => null }))

const opts = {
  activeFilter: null, activeSource: null as string | null, sessionFilter: null,
  toolFilter: null, deviceFilter: null, dateRange: 'all' as const,
}
const response = <T,>(data: T) => ({ ok: true, json: async () => data }) as Response
const row = (n: number): UsageRow => ({
  id: `row-${String(n).padStart(5, '0')}`, ts: 1776420000000000 + n,
  provider: 'test-provider', model: 'test-model', endpoint: 'otlp',
  prompt_tokens: 1, completion_tokens: 1, total_cost_usd: 0, latency_ms: 1,
} as UsageRow)
const rows = (start: number, count: number) => Array.from({ length: count }, (_, i) => row(start - i))
const usageCalls = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls
  .map(([input]) => new URL(String(input), window.location.origin))
  .filter(url => url.pathname === '/usage')

function mockHistory(initial = rows(1000, 300)) {
  let history = initial
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin)
    if (url.pathname !== '/usage') return response([])
    const before = url.searchParams.get('before')
    const [ts, id] = before?.split(':') ?? []
    return response(history.filter(r => !before || r.ts < Number(ts) || (r.ts === Number(ts) && r.id < id)).slice(0, 100))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, setHistory: (next: UsageRow[]) => { history = next } }
}

const intersections = new Set<{ callback: IntersectionObserverCallback; target: Element }>()
async function intersectSentinel() {
  await act(async () => {
    for (const { callback, target } of [...intersections]) {
      callback([{ isIntersecting: true, target } as IntersectionObserverEntry], {} as IntersectionObserver)
    }
  })
}

beforeEach(() => {
  app.refreshTrigger = 0
  app.activeSource = null
  app.setError.mockClear()
  localStorage.clear()
  sessionStorage.clear()
  intersections.clear()
  vi.stubGlobal('IntersectionObserver', class {
    registration: { callback: IntersectionObserverCallback; target: Element } | null = null
    constructor(private callback: IntersectionObserverCallback) {}
    observe(target: Element) { this.registration = { callback: this.callback, target }; intersections.add(this.registration) }
    disconnect() { if (this.registration) intersections.delete(this.registration) }
  })
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  // Give the real virtualizer a viewport and row geometry; do not mock its range calculation.
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) { return this.tagName === 'TBODY' ? (this.querySelector('.expanded-row') ? 252 : 52) : 520 })
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(100000)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(520)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1600)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    return { width: 1600, height: this.tagName === 'TBODY' ? (this.querySelector('.expanded-row') ? 252 : 52) : 520, top: 0, left: 0, right: 1600, bottom: 520, x: 0, y: 0, toJSON() {} }
  })
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: function (this: HTMLElement, options: ScrollToOptions) {
    this.scrollTop = options.top ?? 0
    this.dispatchEvent(new Event('scroll'))
  } })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('Request Logs data', () => {
  it('uses the last row cursor and retains equal-timestamp rows without counts or offsets', async () => {
    const history = rows(1000, 250).map(r => ({ ...r, ts: row(1).ts }))
    const api = mockHistory(history)
    const { result } = renderHook(() => useLogsData(opts))
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    await act(async () => { await result.current.loadMore() })
    expect(result.current.usageRows.length).toBe(200)
    expect(usageCalls(api.fetchMock)[1].searchParams.get('before')).toBe(`${history[99].ts}:${history[99].id}`)
    await act(async () => { await result.current.loadMore() })
    expect(result.current.usageRows).toEqual(history)
    expect(result.current.hasMore).toBe(false)
    expect(api.fetchMock.mock.calls.some(([url]) => String(url).includes('/usage/count'))).toBe(false)
    expect(usageCalls(api.fetchMock).every(url => url.searchParams.get('limit') === '100' && !url.searchParams.has('offset'))).toBe(true)
  })

  it('resets on a filter change and ignores a stale pending page', async () => {
    const api = mockHistory()
    const { result, rerender } = renderHook((props) => useLogsData(props), { initialProps: opts })
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    let resolve!: (value: Response) => void
    api.fetchMock.mockImplementationOnce(() => new Promise(r => { resolve = r }))
    let pending!: Promise<void>
    act(() => { pending = result.current.loadMore() })
    const reset = result.current.scrollReset
    api.setHistory(rows(500, 3))
    rerender({ ...opts, activeSource: 'codex' })
    await waitFor(() => expect(result.current.usageRows).toEqual(rows(500, 3)))
    await act(async () => { resolve(response(rows(900, 100))); await pending })
    expect(result.current.usageRows).toEqual(rows(500, 3))
    expect(result.current.scrollReset).toBeGreaterThan(reset)
    const last = usageCalls(api.fetchMock).at(-1)!
    expect(last.searchParams.get('client_source')).toBe('codex')
    expect(last.searchParams.has('before')).toBe(false)
  })

  it('resets accumulated history when the date range changes', async () => {
    const api = mockHistory()
    const { result, rerender } = renderHook((props: Parameters<typeof useLogsData>[0]) => useLogsData(props), { initialProps: opts })
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    await act(async () => { await result.current.loadMore() })
    expect(result.current.usageRows.length).toBe(200)
    const reset = result.current.scrollReset
    rerender({ ...opts, dateRange: '24h' })
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    expect(result.current.scrollReset).toBeGreaterThan(reset)
    expect(usageCalls(api.fetchMock).at(-1)!.searchParams.has('since')).toBe(true)
    expect(usageCalls(api.fetchMock).at(-1)!.searchParams.has('before')).toBe(false)
  })

  it('refresh prepends a 250-row gap in three requests and preserves loaded rows and cost patches', async () => {
    const history = rows(1000, 300)
    const api = mockHistory(history)
    const { result, rerender } = renderHook(() => useLogsData(opts))
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    await act(async () => { await result.current.loadMore() })
    act(() => result.current.setUsageRows(data => data.map(r => r.id === row(999).id ? { ...r, total_cost_usd: 12 } : r)))
    const reset = result.current.scrollReset
    api.setHistory([...rows(1250, 250), ...history])
    const count = usageCalls(api.fetchMock).length
    app.refreshTrigger++
    rerender()
    await waitFor(() => expect(result.current.usageRows.length).toBe(450))
    expect(usageCalls(api.fetchMock).length - count).toBe(3)
    expect(result.current.usageRows[0].id).toBe(row(1250).id)
    expect(result.current.usageRows.find(r => r.id === row(999).id)?.total_cost_usd).toBe(12)
    expect(result.current.scrollReset).toBe(reset)
    await act(async () => { await result.current.loadMore() })
    expect(result.current.usageRows.length).toBe(550)
    expect(usageCalls(api.fetchMock).at(-1)!.searchParams.get('before')).toBe(`${row(801).ts}:${row(801).id}`)
  })

  it('replaces and resets to the top after five entirely new pages', async () => {
    const history = rows(1000, 300)
    const api = mockHistory(history)
    const { result, rerender } = renderHook(() => useLogsData(opts))
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    const reset = result.current.scrollReset
    api.setHistory([...rows(1600, 600), ...history])
    const count = usageCalls(api.fetchMock).length
    app.refreshTrigger++
    rerender()
    await waitFor(() => expect(result.current.usageRows.length).toBe(500))
    expect(usageCalls(api.fetchMock).length - count).toBe(5)
    expect(result.current.usageRows).toEqual(rows(1600, 500))
    expect(result.current.scrollReset).toBeGreaterThan(reset)
    expect(result.current.hasMore).toBe(true)
  })

  it('leaves existing rows alone when a refresh has no new head, including late timestamps', async () => {
    const history = rows(1000, 100)
    const api = mockHistory(history)
    const { result, rerender } = renderHook(() => useLogsData(opts))
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    api.setHistory([history[0], { ...row(999), id: 'late' }, ...history.slice(1)])
    app.refreshTrigger++
    rerender()
    await waitFor(() => expect(usageCalls(api.fetchMock).length).toBe(2))
    await waitFor(() => expect(result.current.logsLoading).toBe(false))
    expect(result.current.usageRows).toEqual(history)
  })

  it('deduplicates simultaneous load requests and permits retry after failure', async () => {
    const api = mockHistory()
    const { result } = renderHook(() => useLogsData(opts))
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    api.fetchMock.mockRejectedValueOnce(new Error('offline'))
    await act(async () => { await Promise.all([result.current.loadMore(), result.current.loadMore()]) })
    expect(result.current.usageRows.length).toBe(100)
    expect(result.current.loadMoreFailed).toBe(true)
    await act(async () => { await result.current.loadMore() })
    expect(result.current.usageRows.length).toBe(200)
    expect(result.current.loadMoreFailed).toBe(false)
  })
})

describe('Request Logs table', () => {
  it('loads from the sentinel and renders only a window after accumulating a long history', async () => {
    const api = mockHistory(rows(2000, 1000))
    render(createElement(StrictMode, null, createElement(LogsPage)))
    await waitFor(() => expect(intersections.size).toBe(1))
    const before = usageCalls(api.fetchMock).length
    await intersectSentinel()
    await waitFor(() => expect(usageCalls(api.fetchMock).length).toBe(before + 1))
    expect(usageCalls(api.fetchMock).at(-1)!.searchParams.get('before')).toBe(`${row(1901).ts}:${row(1901).id}`)
    for (let i = 0; i < 8; i++) {
      await waitFor(() => expect(intersections.size).toBe(1))
      await intersectSentinel()
    }
    await waitFor(() => expect(intersections.size).toBe(1))
    expect(document.querySelectorAll('tr.expandable-row').length).toBeGreaterThan(0)
    expect(document.querySelectorAll('tr.expandable-row').length).toBeLessThan(40)
    const viewport = screen.getByTestId('logs-sentinel').parentElement!
    fireEvent.scroll(viewport, { target: { scrollTop: 25000 } })
    await waitFor(() => expect(document.querySelector('tbody[data-index="480"]')).toBeTruthy())
    expect(document.querySelectorAll('tr.expandable-row').length).toBeLessThan(40)
    await intersectSentinel()
    expect(await screen.findByText('No more logs')).toBeTruthy()
  })

  it('pauses the observer after a failed page and retries from the same cursor on demand', async () => {
    const api = mockHistory()
    render(createElement(LogsPage))
    await waitFor(() => expect(intersections.size).toBe(1))
    api.fetchMock.mockRejectedValueOnce(new Error('offline'))
    await intersectSentinel()
    const retry = await screen.findByRole('button', { name: 'Retry' })
    expect(intersections.size).toBe(0)
    const failedCursor = usageCalls(api.fetchMock).at(-1)!.searchParams.get('before')
    fireEvent.click(retry)
    await waitFor(() => expect(intersections.size).toBe(1))
    expect(usageCalls(api.fetchMock).at(-1)!.searchParams.get('before')).toBe(failedCursor)
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('keeps the visible row anchored during a refresh prepend and scrolls to top on filter change', async () => {
    const history = rows(1000, 300)
    const api = mockHistory(history)
    const view = render(createElement(LogsPage))
    await waitFor(() => expect(intersections.size).toBe(1))
    const viewport = screen.getByTestId('logs-sentinel').parentElement!
    fireEvent.scroll(viewport, { target: { scrollTop: 1040 } })
    api.setHistory([...rows(1010, 10), ...history])
    app.refreshTrigger++
    view.rerender(createElement(LogsPage))
    await waitFor(() => expect(viewport.scrollTop).toBe(1560))
    app.activeSource = 'codex'
    view.rerender(createElement(LogsPage))
    await waitFor(() => expect(viewport.scrollTop).toBe(0))
    expect(usageCalls(api.fetchMock).at(-1)!.searchParams.has('before')).toBe(false)
  })
})
