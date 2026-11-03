// @vitest-environment jsdom
import { createElement } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../src/App'
import { trendBarHeight } from '../src/charts/trendScale'
import { fillGaps, resolveTimezone } from '../src/utils'

type Reply = unknown | 'fail' | 'pending'
const ME = { provider: 'google', user: { id: 'u', email: 'a@example.com' } }
const SUMMARY = [{ provider: 'p', model: 'm', requests: 2, prompt_tokens: 10, completion_tokens: 5, cached_tokens: 0, cache_creation_tokens: 0, total_tokens: 15, total_cost_usd: 1.5, latency_sum_ms: 100 }]

/** Stub fetch by path; `replies` may switch over time (it is read per call). */
function stubApi(replies: Record<string, Reply>) {
  const calls: string[] = []
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin)
    calls.push(url.pathname + url.search)
    const reply = url.pathname in replies ? replies[url.pathname]
      : url.pathname === '/auth/me' ? ME
      : url.pathname === '/usage/count' ? { total: 5 }
      : url.pathname === '/version' ? { version: '1' }
      : []
    if (reply === 'pending') return new Promise<Response>(() => {})
    if (reply === 'fail') return { ok: false, status: 500, json: async () => ({}) } as Response
    return { ok: true, status: 200, json: async () => reply } as Response
  }))
  return calls
}

const open = (path: string) => {
  window.history.replaceState({}, '', path)
  render(createElement(App))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear() })

it('keeps trend bars finite and inside the plot whatever the max', () => {
  expect(trendBarHeight(5, 10)).toBe(100)
  expect(trendBarHeight(5, 0)).toBe(0)
  expect(trendBarHeight(5, NaN)).toBe(0)
  expect(trendBarHeight(5, undefined as unknown as number)).toBe(0)
  expect(trendBarHeight(1e12, 1)).toBe(200)
  expect(trendBarHeight(-3, 10)).toBe(0)
})

it('Overview: a failed summary shows an error with Retry, not zeros, and Retry reloads', async () => {
  const replies: Record<string, Reply> = { '/usage/summary': 'fail' }
  stubApi(replies)
  open('/dashboard')
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('Failed to fetch dashboard data')
  expect(screen.queryByText('Estimated Cost')).toBeNull()
  expect(screen.queryByText('No data available')).toBeNull()
  replies['/usage/summary'] = SUMMARY
  await act(async () => { fireEvent.click(within(alert).getByRole('button', { name: 'Retry' })) })
  expect(await screen.findByText('Estimated Cost')).toBeTruthy()
  expect(screen.queryByRole('alert')).toBeNull()
})

it('Overview: a summary in flight shows a loading state, not zeros', async () => {
  stubApi({ '/usage/summary': 'pending' })
  open('/dashboard')
  expect(await screen.findByRole('status')).toBeTruthy()
  expect(screen.queryByText('Estimated Cost')).toBeNull()
  expect(screen.queryByText('$0.00')).toBeNull()
})

it('Tool Calls: never shows the previous range under a new one; failures offer Retry', async () => {
  const replies: Record<string, Reply> = { '/usage/summary': SUMMARY, '/usage/by-tool': [{ tool_name: 'bash', count: 7 }] }
  stubApi(replies)
  open('/dashboard')
  const panel = () => screen.getByText('Tool Calls').closest('.widget') as HTMLElement
  await waitFor(() => expect(within(panel()).getByText('bash')).toBeTruthy())

  replies['/usage/by-tool'] = 'fail'
  await act(async () => { fireEvent.change(screen.getByLabelText('Date Range'), { target: { value: '5h' } }) })
  await waitFor(() => expect(within(panel()).getByRole('alert').textContent).toContain('Failed to load tool calls'))
  expect(within(panel()).queryByText('bash')).toBeNull()

  replies['/usage/by-tool'] = 'pending'
  await act(async () => { fireEvent.click(within(panel()).getByRole('button', { name: 'Retry' })) })
  expect(within(panel()).getByRole('status')).toBeTruthy()
  expect(within(panel()).queryByText('bash')).toBeNull()
  expect(within(panel()).queryByText('No data available')).toBeNull()
})

it('Settings → Clients: a failed device read shows errors with Retry, not Loading forever', async () => {
  const replies: Record<string, Reply> = { '/auth/devices': 'fail' }
  stubApi(replies)
  open('/settings?section=devices')
  await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(2))
  expect(screen.queryByText('Loading...')).toBeNull()
  replies['/auth/devices'] = { devices: [{ id: 'm', kind: 'client', device_name: 'Laptop', created_at: 1, last_used_at: null, current: false }], web_sessions: [] }
  await act(async () => { fireEvent.click(screen.getAllByRole('button', { name: 'Retry' })[0]) })
  expect(await screen.findByText('Laptop')).toBeTruthy()
})

it('Pricing: a slow read shows loading and no counts; a failed one offers Retry', async () => {
  const replies: Record<string, Reply> = { '/pricing': 'pending' }
  stubApi(replies)
  open('/settings?section=pricing')
  expect(await screen.findByRole('status')).toBeTruthy()
  expect(screen.queryByText('No models for this source yet.')).toBeNull()
  cleanup()
  replies['/pricing'] = 'fail'
  stubApi(replies)
  open('/settings?section=pricing')
  await waitFor(() => expect(screen.getAllByRole('alert').some(a => a.textContent?.includes('Failed to load pricing') && within(a).queryByRole('button', { name: 'Retry' }))).toBe(true))
  // Shown once, by the table, not again as a page-wide error.
  expect(screen.getAllByText(/Failed to load pricing/)).toHaveLength(1)
})

it('Overview: only a failed summary replaces it; heatmap and count fail on their own', async () => {
  stubApi({ '/usage/summary': SUMMARY, '/usage/count': 'fail' })
  const inner = globalThis.fetch
  // The heatmap is the /usage/daily read with an `until`.
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
    String(input).includes('/usage/daily?') && String(input).includes('until=')
      ? { ok: false, status: 500, json: async () => ({}) } as Response
      : inner(input)))
  open('/dashboard')
  expect(await screen.findByText('Estimated Cost')).toBeTruthy()
  const alerts = await screen.findAllByRole('alert')
  expect(alerts.map(a => a.textContent)).toEqual([expect.stringContaining('Failed to load activity')])
  expect(screen.queryByText('Failed to fetch dashboard data')).toBeNull()
})

it('Overview: Refresh keeps Tool Calls rows; a new range keeps the layout but hides the old KPIs until its summary lands', async () => {
  const replies: Record<string, Reply> = { '/usage/summary': SUMMARY, '/usage/by-tool': [{ tool_name: 'bash', count: 7 }] }
  stubApi(replies)
  open('/dashboard')
  const panel = () => screen.getByText('Tool Calls').closest('.widget') as HTMLElement
  await waitFor(() => expect(within(panel()).getByText('bash')).toBeTruthy())

  replies['/usage/by-tool'] = 'pending'
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh' })) })
  expect(within(panel()).getByText('bash')).toBeTruthy()
  expect(within(panel()).queryByRole('status')).toBeNull()

  replies['/usage/summary'] = 'pending'
  const costTile = () => screen.getByText('Estimated Cost', { selector: '.stat-label' }).parentElement as HTMLElement
  const oldCost = costTile().querySelector('.stat-value')!.textContent
  expect(oldCost).toMatch(/^\$/)
  await act(async () => { fireEvent.change(screen.getByLabelText('Date Range'), { target: { value: '5h' } }) })
  // The overview stays mounted (no full-page Loading); the tiles show a placeholder, not the old totals.
  expect(costTile().querySelector('.stat-value')!.textContent).toBe('—')
  expect(costTile().querySelector('.stat-detail')!.textContent).toBe('—')
  expect(screen.getByText('Tool Calls')).toBeTruthy()
  // Top Models is built from the summary too: it waits rather than list the old range.
  const topModels = screen.getByText('Top Models').closest('.widget') as HTMLElement
  expect(within(topModels).getByRole('status')).toBeTruthy()
  expect(within(topModels).queryByText('m')).toBeNull()
  expect(document.querySelector('.dashboard-refresh-surface.is-refreshing')).toBeTruthy()
  expect(screen.queryByText(oldCost!)).toBeNull()

  // The tiles fill in once the new range's summary lands.
  replies['/usage/summary'] = SUMMARY
  await act(async () => { fireEvent.change(screen.getByLabelText('Date Range'), { target: { value: '7d' } }) })
  await waitFor(() => expect(costTile().querySelector('.stat-value')!.textContent).not.toBe('—'))
  expect(document.querySelector('.dashboard-refresh-surface.is-refreshing')).toBeNull()
})

it('ReadState keeps the Retry button, and its focus, while the retry runs', async () => {
  const replies: Record<string, Reply> = { '/usage/summary': SUMMARY, '/usage/by-tool': 'fail' }
  stubApi(replies)
  open('/dashboard')
  const panel = () => screen.getByText('Tool Calls').closest('.widget') as HTMLElement
  const retry = await waitFor(() => within(panel()).getByRole('button', { name: 'Retry' }))
  retry.focus()
  replies['/usage/by-tool'] = 'pending'
  await act(async () => { fireEvent.click(retry) })
  expect(within(panel()).getByRole('status')).toBeTruthy()
  expect(document.activeElement).toBe(retry)
  expect(retry.getAttribute('aria-disabled')).toBe('true')
})

it('Pricing: a failed "load more" error clears when the next first page loads', async () => {
  const model = (name: string) => ({ name, input: 1, output: 1, cache_read: 0, cache_write: null, source: 'litellm' })
  const calls = stubApi({})
  const inner = globalThis.fetch
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin)
    if (url.pathname !== '/pricing') return inner(input)
    calls.push(url.pathname + url.search)
    if (url.searchParams.get('offset') !== '0') return { ok: false, status: 500, json: async () => ({}) } as Response
    return { ok: true, status: 200, json: async () => ({ models: [model(url.searchParams.get('q') || 'a')], total: url.searchParams.get('q') ? 1 : 2, counts: {} }) } as Response
  }))
  // The list end is always in view, so the second page is requested at once.
  vi.stubGlobal('IntersectionObserver', class {
    constructor(private cb: IntersectionObserverCallback) {}
    observe() { this.cb([{ isIntersecting: true } as IntersectionObserverEntry], this as unknown as IntersectionObserver) }
    disconnect() {}
  })
  open('/settings?section=pricing')
  await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('Failed to load pricing'))
  await act(async () => { fireEvent.change(screen.getByPlaceholderText('Search models...'), { target: { value: 'zz' } }) })
  await waitFor(() => expect(screen.getByText('zz')).toBeTruthy())
  expect(screen.queryByText('Failed to load pricing')).toBeNull()
})

/** Stub the trend reads apart: the daily totals, the 2-year heatmap and the model split. */
function stubTrend(trend: { daily: Reply; split: Reply }) {
  stubApi({ '/usage/summary': SUMMARY })
  const inner = globalThis.fetch
  // The last hours of the default 24-hour range, labelled the way the chart buckets them.
  const periods = fillGaps([], 'hour', 24, resolveTimezone('auto')).slice(-3).map(row => row.period)
  const DAILY = periods.map(period => ({ period, requests: 4, total_tokens: 15, total_cost_usd: 1.5, avg_throughput: 2 }))
  const SPLIT = periods.map(period => ({ period, dimension: 'm', total_tokens: 15, total_cost_usd: 1.5, requests: 4 }))
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin)
    const reply = url.pathname === '/usage/daily' && !url.searchParams.has('until') ? trend.daily
      : url.pathname === '/usage/daily-by-dimension' && url.searchParams.get('dimension') === 'model' ? trend.split
      : undefined
    if (reply === undefined) return inner(input)
    if (reply === 'pending') return new Promise<Response>(() => {})
    if (reply === 'fail') return { ok: false, status: 500, json: async () => ({}) } as Response
    const body = reply === 'daily' ? DAILY : reply === 'split' ? SPLIT : reply
    return { ok: true, status: 200, json: async () => body } as Response
  }))
}
const trendPanel = () => (screen.getByRole('button', { name: /requests$/i }).closest('.widget')) as HTMLElement

it('Trend: waits until both the totals and the model split belong to the new range (sparklines too)', async () => {
  const trend: { daily: Reply; split: Reply } = { daily: 'daily', split: 'split' }
  stubTrend(trend)
  open('/dashboard')
  await waitFor(() => expect(trendPanel().querySelector('.trend-segment')).toBeTruthy())
  const costTile = () => screen.getByText('Estimated Cost', { selector: '.stat-label' }).parentElement as HTMLElement
  await waitFor(() => expect(costTile().querySelector('svg')).toBeTruthy())

  // The split for the new range lands at once, its totals never do.
  trend.daily = 'pending'
  await act(async () => { fireEvent.change(screen.getByLabelText('Date Range'), { target: { value: '5h' } }) })
  await waitFor(() => expect(within(costTile()).queryAllByText('—')).toHaveLength(0))
  expect(within(trendPanel()).getByRole('status')).toBeTruthy()
  expect(trendPanel().querySelector('.trend-segment')).toBeNull()
  // The new summary is shown, but not next to the old range's sparkline.
  expect(costTile().querySelector('svg')).toBeNull()
})

it('Trend: one failed read keeps the chart with what loaded and a scoped Retry', async () => {
  stubTrend({ daily: 'daily', split: 'fail' })
  open('/dashboard')
  const alert = await waitFor(() => within(trendPanel()).getByRole('alert'))
  expect(alert.textContent).toContain('Failed to load model breakdown')
  expect(within(alert).getByRole('button', { name: 'Retry' })).toBeTruthy()
  // The totals still draw the requests line, and the panel is not replaced.
  expect(trendPanel().querySelector('polyline.trend-requests')).toBeTruthy()
  cleanup()

  stubTrend({ daily: 'fail', split: 'split' })
  open('/dashboard')
  await waitFor(() => expect(within(trendPanel()).getByRole('alert').textContent).toContain('Failed to load usage trend'))
  // The model split still draws its bars; no requests line from missing totals.
  expect(trendPanel().querySelector('.trend-segment')).toBeTruthy()
  expect(trendPanel().querySelector('polyline.trend-requests')).toBeNull()
})

it('Trend: a new read clears the previous failure and shows loading', async () => {
  const trend: { daily: Reply; split: Reply } = { daily: 'fail', split: 'fail' }
  stubTrend(trend)
  open('/dashboard')
  await waitFor(() => expect(within(trendPanel()).getByRole('alert').textContent).toContain('Failed to load usage trend'))
  trend.daily = 'pending'
  trend.split = 'split'
  await act(async () => { fireEvent.change(screen.getByLabelText('Date Range'), { target: { value: '5h' } }) })
  expect(within(trendPanel()).queryByRole('alert')).toBeNull()
  expect(within(trendPanel()).getByRole('status')).toBeTruthy()
})
