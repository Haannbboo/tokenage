// @vitest-environment jsdom
import { createElement } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App'

const response = (data: any) => ({ ok: true, status: 200, json: async () => data }) as Response
function mockApi(options: { user?: boolean; total?: number } = {}) {
  let total = options.total ?? 1
  let summaryGate: Promise<Response> | null = null
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname
    if (path === '/auth/me') return Promise.resolve(response({ provider: 'google', user: options.user === false ? null : { id: '1', email: 'test@example.com' } }))
    if (path === '/usage/count') return Promise.resolve(response({ total }))
    if (path === '/usage/summary' && summaryGate) return summaryGate
    if (path === '/usage') return Promise.resolve(response([{ model: 'test-model', prompt_tokens: 1, completion_tokens: 1, total_cost_usd: 0, latency_ms: 1 }]))
    if (path === '/devices/status') return Promise.resolve(response({ devices: [{ reported_at: '2026-01-01', status: { detected: {}, agents: {} } }] }))
    return Promise.resolve(response([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, setTotal: (value: number) => { total = value }, holdSummary: (gate: Promise<Response>) => { summaryGate = gate } }
}
const copyTestCommand = () => {
  const command = screen.getByText('tokenage claude')
  fireEvent.click(within(command.parentElement!.parentElement!).getByRole('button', { name: /copy/i }))
}
const countCalls = (api: ReturnType<typeof mockApi>, path: string) => api.fetchMock.mock.calls.filter(([url]) => String(url).includes(path)).length
beforeEach(() => {
  window.history.replaceState({}, '', '/dashboard')
  localStorage.clear()
  sessionStorage.clear()
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

describe('dashboard interactions', () => {
  it('gates an unauthenticated session', async () => {
    const api = mockApi({ user: false })
    render(createElement(App))
    expect(await screen.findByRole('link', { name: /sign in/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /refresh/i })).toBeNull()
    expect(countCalls(api, '/usage/summary')).toBe(0)
  })
  it('renders the app for an authenticated session and retains content during refresh', async () => {
    const api = mockApi()
    render(createElement(App))
    const refresh = await screen.findByRole('button', { name: /refresh/i }) as HTMLButtonElement
    await waitFor(() => expect(refresh.disabled).toBe(false))
    // Anchor on the content structure, not on a label: the copy can change.
    await waitFor(() => expect(document.querySelector('.widgets-grid')).toBeTruthy())
    const content = document.querySelector('.widgets-grid')?.firstElementChild
    let resolve!: (value: Response) => void
    api.holdSummary(new Promise(r => { resolve = r }))
    const previous = countCalls(api, '/usage/summary')
    const previousModels = countCalls(api, '/usage/daily-by-dimension')
    fireEvent.click(refresh)
    await waitFor(() => expect(refresh.disabled).toBe(true))
    expect(countCalls(api, '/usage/summary')).toBe(previous + 1)
    await waitFor(() => expect(countCalls(api, '/usage/daily-by-dimension')).toBe(previousModels + 1))
    expect(document.querySelector('.widgets-grid')?.firstElementChild).toBe(content)
    await act(async () => { resolve(response([{ provider: 'test-provider', model: 'test-model', requests: 7 }])) })
    await waitFor(() => expect(refresh.disabled).toBe(false))
    expect(document.querySelector('.widgets-grid')?.firstElementChild).toBe(content)
    fireEvent.click(screen.getByRole('button', { name: /all models/i }))
    expect(screen.getByText('test-provider')).toBeTruthy()
  })
  it('starts verification and stops polling after a first event', async () => {
    const api = mockApi({ total: 0 })
    vi.useFakeTimers()
    await act(async () => { render(createElement(App)) })
    expect(screen.getByText(/waiting for your first event/i)).toBeTruthy()
    const before = countCalls(api, '/usage/count')
    copyTestCommand()
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(countCalls(api, '/usage/count')).toBeGreaterThan(before)
    api.setTotal(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(countCalls(api, '/usage?limit=1')).toBe(1)
    expect(screen.getByRole('button', { name: /refresh/i })).toBeTruthy()
    const afterSuccess = countCalls(api, '/usage/count')
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(countCalls(api, '/usage/count')).toBe(afterSuccess)
  })
  it('shows timeout guidance and stops polling', async () => {
    const api = mockApi({ total: 0 })
    vi.useFakeTimers()
    await act(async () => { render(createElement(App)) })
    expect(screen.getByText(/waiting for your first event/i)).toBeTruthy()
    copyTestCommand()
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(screen.queryByText(/no local agent detected/i)).toBeNull()
    await act(async () => { await vi.advanceTimersByTimeAsync(46000) })
    expect(screen.getByText(/no local agent detected/i)).toBeTruthy()
    const count = countCalls(api, '/usage/count')
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(countCalls(api, '/usage/count')).toBe(count)
  })
  it('stops verification polling on unmount', async () => {
    const api = mockApi({ total: 0 })
    vi.useFakeTimers()
    let view!: ReturnType<typeof render>
    await act(async () => { view = render(createElement(App)) })
    expect(screen.getByText(/waiting for your first event/i)).toBeTruthy()
    copyTestCommand()
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    const before = countCalls(api, '/usage/count')
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(countCalls(api, '/usage/count')).toBeGreaterThan(before)
    view.unmount()
    const count = countCalls(api, '/usage/count')
    await act(async () => { await vi.advanceTimersByTimeAsync(6000) })
    expect(countCalls(api, '/usage/count')).toBe(count)
  })
})
