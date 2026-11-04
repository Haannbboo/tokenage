// @vitest-environment jsdom
import { createElement, StrictMode } from 'react'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { UsageRow } from '../src/types'
import { useLogsData } from '../src/hooks/useLogsData'
import { useSessionsData } from '../src/hooks/useSessionsData'
import { useDashboardData } from '../src/hooks/useDashboardData'
import { DEFAULT_REQUEST_LOG_COLUMNS, REQUEST_LOG_COLUMNS, useRequestLogColumns } from '../src/hooks/useRequestLogColumns'
import { SessionSelector } from '../src/components/SessionSelector'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { LogsPage } from '../src/pages/LogsPage'

const logsPage = () => createElement(MemoryRouter, { initialEntries: ['/logs'] }, createElement(LogsPage))

const app = vi.hoisted(() => ({
  refreshTrigger: 0, setError: vi.fn(), showToast: vi.fn(), requestUsageRefresh: vi.fn(),
  activeFilter: null as { models?: string[]; provider?: string } | null, activeSource: null as string | null, activeDevice: null as string | null,
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

const columnHeaders = () => screen.getAllByRole('columnheader').map(header => header.textContent)

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
    if (this.classList.contains('logs-table-head') || this.tagName === 'THEAD') return tableHeaderHeight
    return this.tagName === 'TBODY' ? (this.querySelector('.expanded-row') ? 252 : 52) : 520
  })
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(100000)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(520)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1600)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const rect = (height: number, top: number) => ({ width: 1600, height, top, left: 0, right: 1600, bottom: top + height, x: 0, y: top, toJSON() {} })
    // The filter bar sticks under the navbar; the table starts below it.
    if (this.classList.contains('filter-bar')) return rect(100, 0)
    // The header table sits on top of the body table, which holds the rows.
    if (this.tagName === 'TABLE') return rect(520, 300 + (this.closest('.logs-table-head') ? 0 : tableHeaderHeight) - scrollY)
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
    render(logsPage())
    expect(await screen.findByText('Failed to fetch log data')).toBeTruthy()
    // Pinned to the visible part of the table, not centred in its full width.
    expect(screen.getByRole('button', { name: 'Retry' }).closest('.logs-visible')).toBeTruthy()
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

  it('returns focus to session search on Escape but not pointer dismissal', () => {
    render(createElement(SessionSelector, { sessions: [], sessionFilter: null, onChange: vi.fn(), sourceColors: {} }))
    const input = screen.getByRole('textbox', { name: 'Search session ID…' })
    act(() => input.focus())
    act(() => screen.getByRole('button', { name: 'All Sessions' }).focus())
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(document.activeElement).toBe(input)
    expect(screen.queryByRole('button', { name: 'All Sessions' })).toBeNull()
    fireEvent.focus(input)
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('button', { name: 'All Sessions' })).toBeNull()
  })

  it('keeps all log filters independent of the dashboard date range', async () => {
    app.dateRange = '24h'
    const api = mockHistory()
    const view = render(logsPage())
    await waitFor(() => expect(document.querySelector('tbody[data-index]')).toBeTruthy())
    expect(screen.queryByText('Date Range')).toBeNull()
    const filterCalls = () => api.fetchMock.mock.calls
      .map(([input]) => new URL(String(input), window.location.origin))
      .filter(url => ['/usage', '/sessions', '/usage/tools', '/usage/summary', '/usage/sources'].includes(url.pathname))
    expect(new Set(filterCalls().map(url => url.pathname)).size).toBe(5)
    expect(filterCalls().every(url => !url.searchParams.has('since'))).toBe(true)
    const count = filterCalls().length
    app.dateRange = '7d'
    view.rerender(logsPage())
    expect(filterCalls().length).toBe(count)
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull()
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
  })

  it('loads saved columns and ignores the pins older versions stored', () => {
    const key = 'tokenage-request-log-columns'
    localStorage.setItem(key, JSON.stringify({ visibleColumnIds: ['time', 'device'], pinnedColumnIds: ['device', 'speed', 'unknown'] }))
    const saved = renderHook(() => useRequestLogColumns())
    expect(saved.result.current.visibleColumnIds).toEqual(['time', 'device'])
    act(() => saved.result.current.setColumnVisible('speed', true))
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual({ visibleColumnIds: ['time', 'device', 'speed'] })
    act(() => saved.result.current.resetColumns())
    expect(saved.result.current.visibleColumnIds).not.toContain('device')
  })

  it('renders every chosen column at a narrow width, scrolling the rows sideways under a matching header', async () => {
    mockHistory()
    // A phone-width panel, however it is measured.
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(358)
    vi.stubGlobal('ResizeObserver', class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) { queueMicrotask(() => this.callback([{ target, contentRect: { width: 358 } } as ResizeObserverEntry], this as unknown as ResizeObserver)) }
      unobserve() {}
      disconnect() {}
    })
    render(logsPage())
    await waitFor(() => expect(document.querySelector('tbody[data-index]')).toBeTruthy())
    const labels = REQUEST_LOG_COLUMNS.filter(({ id }) => DEFAULT_REQUEST_LOG_COLUMNS.includes(id)).map(({ label }) => label)
    expect(columnHeaders()).toEqual(labels)
    expect(document.querySelector('tbody[data-index] tr')!.children).toHaveLength(labels.length)
    // The rows scroll inside their own container; the header shares their column widths.
    const scroller = document.querySelector<HTMLElement>('.logs-table-scroll')!
    expect(scroller.querySelector('tbody[data-index]')).toBeTruthy()
    const widths = (root: Element) => [...root.querySelectorAll('col')].map(col => (col as HTMLElement).style.width)
    expect(widths(document.querySelector('.logs-table-head')!)).toEqual(widths(scroller))
    // Either one scrolls the other: the header's scrollbar sits under the sticky header.
    const head = document.querySelector<HTMLElement>('.logs-table-head')!
    scroller.scrollLeft = 120
    fireEvent.scroll(scroller)
    expect(head.scrollLeft).toBe(120)
    head.scrollLeft = 40
    fireEvent.scroll(head)
    expect(scroller.scrollLeft).toBe(40)
    // Assistive tech reads the column names from the body table; the visual header is hidden from it.
    expect(head.getAttribute('aria-hidden')).toBe('true')
    expect(screen.getAllByRole('columnheader').every(th => scroller.contains(th) && th.getAttribute('scope') === 'col')).toBe(true)
    // Full-width rows fit the scroller's visible width.
    expect(document.querySelector<HTMLElement>('.panel')!.style.getPropertyValue('--logs-table-visible')).toBe('358px')
    fireEvent.click(screen.getByRole('button', { name: 'Columns' }))
    for (const row of screen.getAllByRole('menuitemcheckbox')) {
      expect(row.getAttribute('aria-checked')).toBe(String(labels.includes(row.lastChild!.textContent!)))
    }
  })

  it('toggles, resets and shows all columns from the Columns menu', async () => {
    mockHistory([])
    render(logsPage())
    await screen.findByText('No requests found for the selected filters.')
    const trigger = screen.getByRole('button', { name: 'Columns' })
    fireEvent.click(trigger)
    const row = (name: string) => screen.getByRole('menuitemcheckbox', { name })
    expect(document.activeElement).toBe(row('Time'))
    fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(row('Model'))
    fireEvent.click(row('Model'))
    expect(row('Model').getAttribute('aria-checked')).toBe('false')
    expect(columnHeaders()).not.toContain('Model')
    fireEvent.click(row('Device'))
    expect(columnHeaders()).toContain('Device')
    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    expect(columnHeaders()).toEqual(REQUEST_LOG_COLUMNS.filter(({ id }) => DEFAULT_REQUEST_LOG_COLUMNS.includes(id)).map(({ label }) => label))
    fireEvent.click(screen.getByRole('button', { name: 'All columns' }))
    expect(columnHeaders()).toEqual(REQUEST_LOG_COLUMNS.map(({ label }) => label))
    expect(screen.getAllByRole('menuitemcheckbox').every(item => item.getAttribute('aria-checked') === 'true')).toBe(true)
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(document.activeElement).toBe(trigger)
  })

  it('jumps with Home and End and closes the Columns menu when focus tabs out', async () => {
    mockHistory([])
    render(logsPage())
    await screen.findByText('No requests found for the selected filters.')
    const trigger = screen.getByRole('button', { name: 'Columns' })
    fireEvent.click(trigger)
    expect(trigger.getAttribute('aria-controls')).toBe(screen.getByRole('menu').parentElement!.id)
    const rows = screen.getAllByRole('menuitemcheckbox')
    fireEvent.keyDown(document.activeElement!, { key: 'End' })
    expect(document.activeElement).toBe(rows[rows.length - 1])
    fireEvent.keyDown(document.activeElement!, { key: 'Home' })
    expect(document.activeElement).toBe(rows[0])
    // Focus moving within the control keeps it open (Shift+Tab to the trigger)...
    fireEvent.focusOut(rows[0], { relatedTarget: trigger })
    expect(screen.getByRole('menu')).toBeTruthy()
    // ...and leaving it closes it (Tab past "All columns").
    const allColumns = screen.getByRole('button', { name: 'All columns' })
    fireEvent.focusOut(allColumns, { relatedTarget: screen.getByRole('button', { name: /refresh/i }) })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('resizes a column to the dragged width and keeps the others, even past the panel width', async () => {
    mockHistory([])
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.tagName === 'TH' ? 100 : 0
    })
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(358)
    render(logsPage())
    const empty = await screen.findByText('No requests found for the selected filters.')
    expect(empty.closest('.logs-visible')).toBeTruthy()
    const head = document.querySelector('.logs-table-head')!
    fireEvent.mouseDown(head.querySelector('th .col-resize-handle')!, { clientX: 0 })
    fireEvent.mouseMove(document, { clientX: 50 })
    fireEvent.mouseUp(document)
    const widths = [...head.querySelectorAll('col')].map(col => (col as HTMLElement).style.width)
    expect(widths[0]).toBe('150px')
    expect(widths.slice(1).every(width => width === '100px')).toBe(true)
  })

  it('keeps spacers in table coordinates and removes the bottom spacer at the last loaded row', async () => {
    tableHeaderHeight = 63
    mockHistory()
    render(logsPage())
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
    // Neither the rows nor the sentinel may become the browser's anchor when another page appends.
    expect(screen.getByTestId('logs-sentinel').style.overflowAnchor).toBe('none')
    expect(document.querySelector<HTMLElement>('.logs-table-scroll')!.style.overflowAnchor).toBe('none')
  })

  it('loads from the sentinel and renders only a window after accumulating a long history', async () => {
    const api = mockHistory(rows(2000, 1000))
    render(createElement(StrictMode, null, logsPage()))
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
    render(logsPage())
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
    const view = render(logsPage())
    await waitFor(() => expect(intersections.size).toBe(1))
    scrollWindowTo(1040)
    app.refreshTrigger++
    view.rerender(logsPage())
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


it('uses the session URL parameter for usage and removes it when cleared', async () => {
  const { fetchMock } = mockHistory([])
  const Location = () => createElement('output', { 'data-testid': 'location' }, useLocation().search)
  render(createElement(MemoryRouter, { initialEntries: ['/logs?session=abc'] }, createElement(LogsPage), createElement(Location)))
  await waitFor(() => expect(usageCalls(fetchMock).some(url => url.searchParams.get('session_id') === 'abc')).toBe(true))
  fireEvent.click(screen.getByRole('button', { name: 'Clear session filter' }))
  await waitFor(() => expect(usageCalls(fetchMock).at(-1)?.searchParams.has('session_id')).toBe(false))
  expect(screen.queryByRole('button', { name: 'Clear session filter' })).toBeNull()
  expect(screen.getByTestId('location').textContent).toBe('')
})


it('sends session date, source, model and device filters to the sessions API', async () => {
  app.activeDevice = 'device-1'
  app.activeFilter = { models: ['model-1'] }
  const { fetchMock } = mockHistory([])
  renderHook(() => useSessionsData({ activeSource: 'agent-1', dateRange: '7d', bucket: null }))
  await waitFor(() => {
    const call = fetchMock.mock.calls.map(([input]) => new URL(String(input), window.location.origin)).find(url => url.pathname === '/sessions')
    expect(call?.searchParams.get('client_source')).toBe('agent-1')
    expect(call?.searchParams.get('device_id')).toBe('device-1')
    expect(call?.searchParams.get('model')).toBe('model-1')
    expect(call?.searchParams.get('since')).toBeTruthy()
  })
  app.activeDevice = null
  app.activeFilter = null
})
