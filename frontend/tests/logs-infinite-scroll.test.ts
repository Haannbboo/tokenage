// @vitest-environment jsdom
import { createElement, StrictMode } from 'react'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UsageRow } from '../src/types'
import { useLogsData } from '../src/hooks/useLogsData'
import { useDashboardData } from '../src/hooks/useDashboardData'
import { useRequestLogColumns } from '../src/hooks/useRequestLogColumns'
import { SessionSelector } from '../src/components/SessionSelector'
import { LogsPage } from '../src/pages/LogsPage'

const app = vi.hoisted(() => ({
  refreshTrigger: 0, setError: vi.fn(), showToast: vi.fn(), requestUsageRefresh: vi.fn(),
  activeFilter: null as { models?: string[]; provider?: string } | null, activeSource: null as string | null, activeDevice: null,
  dateRange: 'all', timezone: 'UTC', setActiveFilter: vi.fn(), setActiveSource: vi.fn(),
  setActiveDevice: vi.fn(), setDateRange: vi.fn(),
}))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => app }))
vi.mock('../src/components/DeviceSelect', () => ({ DeviceSelect: () => null }))

const opts = {
  activeFilter: null, activeSource: null as string | null, sessionFilter: null,
  toolFilter: null, deviceFilter: null,
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
    if (url.pathname === '/sessions') return response({ sessions: [] })
    if (url.pathname !== '/usage') return response([])
    const before = url.searchParams.get('before')
    const [ts, id] = before?.split(':') ?? []
    return response(history.filter(r => !before || r.ts < Number(ts) || (r.ts === Number(ts) && r.id < id)).slice(0, 100))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, setHistory: (next: UsageRow[]) => { history = next } }
}

const intersections = new Set<{ callback: IntersectionObserverCallback; target: Element }>()
let scrollY = 0
let tableHeaderHeight = 40
const scrollWindowTo = (top: number) => {
  act(() => {
    scrollY = top
    window.dispatchEvent(new Event('scroll'))
  })
}
async function intersectSentinel() {
  await act(async () => {
    for (const { callback, target } of [...intersections]) {
      callback([{ isIntersecting: true, target } as IntersectionObserverEntry], {} as IntersectionObserver)
    }
  })
}

function mockTableWidth(initialWidth: number) {
  const observers = new Map<Element, ResizeObserverCallback>()
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) {
      if (!target.classList.contains('panel-body')) return
      observers.set(target, this.callback)
      queueMicrotask(() => {
        if (observers.has(target)) this.callback([{ target, contentRect: { width: initialWidth } } as ResizeObserverEntry], {} as ResizeObserver)
      })
    }
    disconnect() { observers.clear() }
  })
  return (width: number) => act(() => {
    for (const [target, callback] of observers) {
      callback([{ target, contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver)
    }
  })
}

const columnHeaders = () => screen.getAllByRole('columnheader').map(header => header.textContent)
const columnCheckbox = (label: string) => screen.getByRole('checkbox', {
  name: name => name.startsWith(label),
}) as HTMLInputElement

beforeEach(() => {
  app.refreshTrigger = 0
  app.activeSource = null
  app.dateRange = 'all'
  app.setError.mockClear()
  app.setDateRange.mockClear()
  tableHeaderHeight = 40
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
  // The page scrolls, so the window is the scroll element: give it a
  // geometry and a working scrollTo instead of an inner scroll container.
  scrollY = 0
  Object.defineProperty(window, 'scrollY', { configurable: true, get: () => scrollY })
  Object.defineProperty(window, 'scrollTo', { configurable: true, value: (options: ScrollToOptions) => {
    scrollY = options.top ?? 0
    window.dispatchEvent(new Event('scroll'))
  } })
  // Sticky offsets the page reads from the document element.
  document.documentElement.style.setProperty('--navbar-top', '12px')
  document.documentElement.style.setProperty('--navbar-height', '56px')
  document.documentElement.style.setProperty('--sticky-gap', '8px')
  // Give the real virtualizer a viewport and row geometry; do not mock its range calculation.
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains('filter-bar')) return 100
    if (this.tagName === 'THEAD') return tableHeaderHeight
    return this.tagName === 'TBODY' ? (this.querySelector('.expanded-row') ? 252 : 52) : 520
  })
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(100000)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(520)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1600)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const rect = (height: number, top: number) => ({ width: 1600, height, top, left: 0, right: 1600, bottom: top + height, x: 0, y: top, toJSON() {} })
    // The filter bar sticks under the navbar; the table starts below it.
    if (this.classList.contains('filter-bar')) return rect(100, 0)
    if (this.tagName === 'TABLE') return rect(520, 300 - scrollY)
    if (this.tagName === 'TBODY') return rect(this.querySelector('.expanded-row') ? 252 : 52, 0)
    return rect(520, 0)
  })
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

  it('keeps accumulated history unbounded when the global date range changes', async () => {
    const api = mockHistory()
    const { result, rerender } = renderHook(() => useLogsData(opts))
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    await act(async () => { await result.current.loadMore() })
    expect(result.current.usageRows.length).toBe(200)
    const reset = result.current.scrollReset
    app.dateRange = '24h'
    rerender()
    expect(result.current.usageRows.length).toBe(200)
    expect(result.current.scrollReset).toBe(reset)
    expect(usageCalls(api.fetchMock).length).toBe(2)
    expect(usageCalls(api.fetchMock).every(url => !url.searchParams.has('since'))).toBe(true)
  })

  it('refresh reloads from the head and includes rows arriving behind it', async () => {
    const history = rows(1000, 100)
    const api = mockHistory(history)
    const { result, rerender } = renderHook(() => useLogsData(opts))
    await waitFor(() => expect(result.current.usageRows.length).toBe(100))
    const reset = result.current.scrollReset
    const late = { ...row(999), id: 'late' }
    api.setHistory([history[0], late, ...history.slice(1)])
    app.refreshTrigger++
    rerender()
    await waitFor(() => expect(result.current.usageRows).toContainEqual(late))
    expect(result.current.scrollReset).toBeGreaterThan(reset)
    expect(usageCalls(api.fetchMock).at(-1)!.searchParams.has('before')).toBe(false)
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
  it('shows a safe first-page error and retries the refresh', async () => {
    const api = mockHistory([])
    const fetchHistory = api.fetchMock.getMockImplementation()!
    api.fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      if (new URL(String(input), window.location.origin).pathname === '/usage') throw new Error('secret detail')
      return fetchHistory(input)
    })
    app.requestUsageRefresh.mockClear()
    render(createElement(LogsPage))
    expect(await screen.findByText('Failed to fetch log data')).toBeTruthy()
    expect(screen.queryByText('secret detail')).toBeNull()
    expect(screen.queryByText('No requests found for the selected filters.')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(app.requestUsageRefresh).toHaveBeenCalledOnce()
  })

  it('keeps the session dropdown searchable and selectable', () => {
    const onChange = vi.fn()
    render(createElement(SessionSelector, {
      sessions: ['alpha', 'beta'].map(session_id => ({ session_id, client_source: 'codex', request_count: 1, started: row(1).ts })),
      sessionFilter: null, onChange, sourceColors: {},
    }))
    const input = screen.getByRole('textbox', { name: 'Search session ID…' })
    fireEvent.focus(input)
    expect(screen.getByRole('button', { name: /beta/ })).toBeTruthy()
    fireEvent.change(input, { target: { value: 'alpha' } })
    expect(screen.queryByRole('button', { name: /beta/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /alpha/ }))
    expect(onChange).toHaveBeenCalledWith('alpha')
    expect(screen.queryByRole('button', { name: 'All Sessions' })).toBeNull()
  })

  it('keeps all log filters independent of the dashboard date range', async () => {
    app.dateRange = '24h'
    const api = mockHistory()
    const view = render(createElement(LogsPage))
    await waitFor(() => expect(document.querySelector('tbody[data-index]')).toBeTruthy())
    expect(screen.queryByText('Date Range')).toBeNull()
    const filterCalls = () => api.fetchMock.mock.calls
      .map(([input]) => new URL(String(input), window.location.origin))
      .filter(url => ['/usage', '/sessions', '/usage/tools', '/usage/summary', '/usage/sources'].includes(url.pathname))
    expect(new Set(filterCalls().map(url => url.pathname)).size).toBe(5)
    expect(filterCalls().every(url => !url.searchParams.has('since'))).toBe(true)
    const count = filterCalls().length
    app.dateRange = '7d'
    view.rerender(createElement(LogsPage))
    expect(filterCalls().length).toBe(count)
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    expect(app.setDateRange).not.toHaveBeenCalled()
  })

  it('keeps the default dashboard summary and sources scoped to the global date range', async () => {
    app.dateRange = '24h'
    const api = mockHistory([])
    const { result, rerender } = renderHook(() => useDashboardData({ includeDaily: false, includeHeatmap: false, includeCount: false }))
    await waitFor(() => expect(result.current.dashboardInitialLoading).toBe(false))
    const filterCalls = () => api.fetchMock.mock.calls
      .map(([input]) => new URL(String(input), window.location.origin))
      .filter(url => ['/usage/summary', '/usage/sources'].includes(url.pathname))
    expect(filterCalls()).toHaveLength(2)
    expect(filterCalls().every(url => url.searchParams.has('since'))).toBe(true)
    const since = filterCalls()[0].searchParams.get('since')!
    app.dateRange = '7d'
    rerender()
    await waitFor(() => expect(filterCalls()).toHaveLength(4))
    expect(filterCalls().slice(2).every(url => url.searchParams.get('since')! < since)).toBe(true)
  })

  it('hides Device for new preferences and preserves an explicitly saved Device column', () => {
    const defaults = renderHook(() => useRequestLogColumns())
    expect(defaults.result.current.visibleColumnIds).not.toContain('device')
    defaults.unmount()
    localStorage.setItem('tokenage-request-log-columns', JSON.stringify(['time', 'device']))
    const saved = renderHook(() => useRequestLogColumns())
    expect(saved.result.current.visibleColumnIds).toEqual(['time', 'device'])
    expect(saved.result.current.pinnedColumnIds).toEqual([])
  })

  it('loads column preferences without pins and discards pins for disabled or unknown columns', () => {
    const key = 'tokenage-request-log-columns'
    localStorage.setItem(key, JSON.stringify({ visibleColumnIds: ['time', 'device'] }))
    const saved = renderHook(() => useRequestLogColumns())
    expect(saved.result.current.visibleColumnIds).toEqual(['time', 'device'])
    expect(saved.result.current.pinnedColumnIds).toEqual([])
    saved.unmount()
    localStorage.setItem(key, JSON.stringify({ visibleColumnIds: ['time', 'device'], pinnedColumnIds: ['device', 'speed', 'unknown'] }))
    const pinned = renderHook(() => useRequestLogColumns())
    expect(pinned.result.current.pinnedColumnIds).toEqual(['device'])
    act(() => pinned.result.current.showAllColumns())
    expect(pinned.result.current.pinnedColumnIds).toEqual(pinned.result.current.visibleColumnIds)
    act(() => pinned.result.current.resetColumns())
    expect(pinned.result.current.pinnedColumnIds).toEqual([])
    expect(pinned.result.current.visibleColumnIds).not.toContain('device')
  })

  it('checks only rendered columns and restores auto-hidden columns when the window widens', async () => {
    mockHistory([])
    const resize = mockTableWidth(350)
    localStorage.setItem('tokenage-request-log-columns', JSON.stringify(['time', 'model', 'session', 'speed']))
    render(createElement(LogsPage))
    await screen.findByText('No requests found for the selected filters.')
    fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
    expect(columnHeaders()).toEqual(['Time', 'Model', 'Session'])
    for (const checkbox of screen.getAllByRole('checkbox')) {
      const label = checkbox.closest('label')!.querySelector('span')!.textContent
      expect((checkbox as HTMLInputElement).checked).toBe(columnHeaders().includes(label))
    }
    expect(columnCheckbox('Speed').closest('label')!.textContent).toContain('hidden: narrow window')
    expect(columnCheckbox('Device').closest('label')!.textContent).not.toContain('hidden: narrow window')
    resize(450)
    expect(columnHeaders()).toEqual(['Time', 'Model', 'Session', 'Speed'])
    expect(columnCheckbox('Speed').checked).toBe(true)
    expect(screen.queryByText('hidden: narrow window')).toBeNull()
  })

  it('pins a checked auto-hidden column, drops the next priority instead, persists and unpins on uncheck', async () => {
    mockHistory([])
    mockTableWidth(350)
    const key = 'tokenage-request-log-columns'
    localStorage.setItem(key, JSON.stringify(['time', 'model', 'session', 'speed']))
    const view = render(createElement(LogsPage))
    await screen.findByText('No requests found for the selected filters.')
    fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
    fireEvent.click(columnCheckbox('Speed'))
    expect(columnHeaders()).toEqual(['Time', 'Model', 'Speed'])
    expect(columnCheckbox('Speed').checked).toBe(true)
    expect(columnCheckbox('Session').checked).toBe(false)
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual({
      visibleColumnIds: ['time', 'model', 'session', 'speed'], pinnedColumnIds: ['speed'],
    })
    view.unmount()
    render(createElement(LogsPage))
    await screen.findByText('No requests found for the selected filters.')
    expect(columnHeaders()).toEqual(['Time', 'Model', 'Speed'])
    fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
    fireEvent.click(columnCheckbox('Speed'))
    expect(columnHeaders()).toEqual(['Time', 'Model', 'Session'])
    expect(columnCheckbox('Speed').checked).toBe(false)
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual({
      visibleColumnIds: ['time', 'model', 'session'], pinnedColumnIds: [],
    })
  })

  it('keeps pinned columns when their widths overflow and preserves the resize stability rule', async () => {
    mockHistory([])
    mockTableWidth(100)
    localStorage.setItem('tokenage-request-log-columns', JSON.stringify({
      visibleColumnIds: ['time', 'model', 'session', 'speed'], pinnedColumnIds: ['session', 'speed'],
    }))
    render(createElement(LogsPage))
    await screen.findByText('No requests found for the selected filters.')
    expect(columnHeaders()).toEqual(['Session', 'Speed'])
    const width = screen.getAllByRole('columnheader').reduce((sum, th) => sum + parseFloat(th.style.width), 0)
    expect(width).toBeGreaterThan(100)
    fireEvent.mouseDown(screen.getAllByRole('columnheader')[0].querySelector('.col-resize-handle')!)
    expect(columnHeaders()).toEqual(['Time', 'Model', 'Session', 'Speed'])
    fireEvent.mouseUp(document)
    expect(columnHeaders()).toEqual(['Session', 'Speed'])
  })

  it('keeps spacers in table coordinates and removes the bottom spacer at the last loaded row', async () => {
    tableHeaderHeight = 63
    mockHistory()
    render(createElement(LogsPage))
    await waitFor(() => expect(document.querySelector('tbody[data-index]')).toBeTruthy())
    const assertSpacers = () => {
      const bodies = [...document.querySelectorAll<HTMLTableSectionElement>('tbody[data-index]')]
      const spacers = document.querySelectorAll<HTMLTableCellElement>('tr[aria-hidden] td')
      const first = Number(bodies[0].dataset.index)
      const last = Number(bodies.at(-1)!.dataset.index)
      expect(spacers[0].style.height).toBe(`${first * 52}px`)
      expect(spacers[1].style.height).toBe(`${(99 - last) * 52}px`)
    }
    assertSpacers()
    scrollWindowTo(2000)
    await waitFor(() => expect(Number(document.querySelector<HTMLElement>('tbody[data-index]')!.dataset.index)).toBeGreaterThan(0))
    assertSpacers()
    scrollWindowTo(5000)
    await waitFor(() => expect(document.querySelector('tbody[data-index="99"]')).toBeTruthy())
    assertSpacers()
    // The sentinel must not become the browser's anchor when another page appends.
    expect(screen.getByTestId('logs-sentinel').parentElement!.style.overflowAnchor).toBe('none')
  })

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
    scrollWindowTo(25000)
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

  it('resets the scroll position on refresh', async () => {
    mockHistory()
    const view = render(createElement(LogsPage))
    await waitFor(() => expect(intersections.size).toBe(1))
    scrollWindowTo(1040)
    app.refreshTrigger++
    view.rerender(createElement(LogsPage))
    await waitFor(() => expect(window.scrollY).toBe(124))
  })

})

it('sends every selected model to the logs endpoint', async () => {
  const { fetchMock } = mockHistory([])
  const activeFilter = { models: ['first', 'second'] }
  renderHook(() => useLogsData({ ...opts, activeFilter }))
  await waitFor(() => expect(usageCalls(fetchMock).length).toBeGreaterThan(0))
  expect(usageCalls(fetchMock)[0].searchParams.getAll('model')).toEqual(['first', 'second'])
})


it('sends every selected model to dashboard and heatmap requests', async () => {
  app.activeFilter = { models: ['first', 'second'] }
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin)
    return response(url.pathname === '/usage/count' ? { total: 0 } : [])
  })
  vi.stubGlobal('fetch', fetchMock)
  try {
    renderHook(() => useDashboardData())
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(5))
    const requests = fetchMock.mock.calls.map(([input]) => new URL(String(input), window.location.origin))
    const filtered = requests.filter(url => url.searchParams.has('model'))
    expect(filtered.map(url => url.pathname).sort()).toEqual(['/usage/daily', '/usage/daily', '/usage/summary'])
    expect(filtered.every(url => url.searchParams.getAll('model').join(',') === 'first,second')).toBe(true)
  } finally { app.activeFilter = null }
})


it('sends the provider to logs, dashboard, heatmap and chart filters', async () => {
  app.activeFilter = { provider: 'openai' }
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin)
    return response(url.pathname === '/usage/count' ? { total: 0 } : [])
  })
  vi.stubGlobal('fetch', fetchMock)
  try {
    const { result } = renderHook(() => useDashboardData())
    renderHook(() => useLogsData({ ...opts, activeFilter: { provider: 'openai' } }))
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(6))
    const requests = fetchMock.mock.calls.map(([input]) => new URL(String(input), window.location.origin))
    const filtered = requests.filter(url => url.searchParams.has('provider'))
    expect(filtered.map(url => url.pathname).sort()).toEqual(['/usage', '/usage/daily', '/usage/daily', '/usage/summary'])
    expect(filtered.every(url => url.searchParams.get('provider') === 'openai' && !url.searchParams.has('model'))).toBe(true)
    expect(result.current.dashboardFilterParams.provider).toBe('openai')
  } finally { app.activeFilter = null }
})
