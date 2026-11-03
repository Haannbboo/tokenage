// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SessionsTab } from '../src/pages/SessionsTab'
const state = vi.hoisted(() => ({ dateRange: 'all', activeSource: null as string | null, distribution: null as any, bucket: null as any, sessions: [{ session_id: 'session-123456789', started: 1000000, ended: 2000000, client_source: 'test', request_count: 1, total_cost_usd: 0 }] }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ showToast: vi.fn(), dateRange: state.dateRange, activeSource: state.activeSource, timezone: 'UTC' }) }))
vi.mock('../src/hooks/useSessionsData', () => ({ useSessionsData: (opts: any) => ({ ...(state.bucket = opts.bucket, {}), sessions: state.sessions, distribution: state.distribution, sessionsLoading: false, sessionsError: false, hasMoreSessions: false, sessionSortBy: 'ended', sessionSortOrder: 'desc', handleSessionSort: vi.fn(), setSessionPage: vi.fn() }) }))
vi.mock('../src/components/SessionDetail', () => ({ SessionDetail: () => createElement('div', null, 'detail opened') }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('copies the ID without toggling, while the row and keyboard toggle', () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  const view = render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  const copy = view.container.querySelector('.click-to-copy')!
  const row = copy.closest('tr')!
  fireEvent.click(copy)
  expect(writeText).toHaveBeenCalledWith('session-123456789')
  expect(row.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(row)
  expect(screen.getByText('detail opened')).toBeTruthy()
  fireEvent.keyDown(row, { key: 'Enter' })
  expect(row.getAttribute('aria-expanded')).toBe('false')
})

it('scales activity to listed starts and ends and clips earlier sessions', () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2025-10-22T12:00:00Z'))
  state.dateRange = '24h'
  const time = (hour: number) => Date.UTC(2025, 9, 22, hour) * 1000
  state.sessions = [
    { session_id: 'early', started: time(-13), ended: time(10), client_source: 'test', request_count: 1, total_cost_usd: 0 },
    { session_id: 'first', started: time(8), ended: time(9), client_source: 'test', request_count: 1, total_cost_usd: 0 },
    { session_id: 'last', started: time(10), ended: time(12), client_source: 'test', request_count: 1, total_cost_usd: 0 },
  ]
  const view = render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  const bars = view.container.querySelectorAll<HTMLElement>('.session-span i')
  expect(bars[0].style.left).toBe('0%'); expect(bars[0].style.width).toBe('50%')
  expect(bars[1].style.left).toBe('0%'); expect(bars[1].style.width).toBe('25%')
  expect(bars[2].style.left).toBe('50%'); expect(bars[2].style.width).toBe('50%')
  expect(view.container.querySelector('.session-axis')?.textContent).toMatch(/08:00.*12:00/)
  state.dateRange = 'all'; view.rerender(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(view.container.querySelector('.session-axis')?.textContent).toMatch(/11:00.*12:00/)
  state.dateRange = '24h'
  state.sessions = [{ session_id: 'pre', started: time(-13), ended: time(7), client_source: 'test', request_count: 1, total_cost_usd: 0 }, state.sessions[1]]
  view.rerender(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(view.container.querySelector('.session-axis')?.textContent).toMatch(/07:00.*09:00/)
  state.dateRange = 'all'
  vi.useRealTimers()
})

it('shows a bar for a lone session started before the range', () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2025-10-22T12:00:00Z'))
  state.dateRange = '24h'
  state.sessions = [{ session_id: 'pre', started: Date.parse('2025-10-21T11:00:00Z') * 1000, ended: Date.parse('2025-10-22T07:00:00Z') * 1000, client_source: 'test', request_count: 1, total_cost_usd: 0 }]
  const view = render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(parseFloat(view.container.querySelector<HTMLElement>('.session-span i')!.style.width)).toBeGreaterThan(0)
  state.dateRange = 'all'; vi.useRealTimers()
})

it('labels the descending ended sort as Last used', () => {
  render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(screen.getByRole('button', { name: 'Last used ↓' })).toBeTruthy()
})

it('clears the chart bucket when the date range or source changes', () => {
  state.distribution = { over_time: [{ key: 'a', period: '2025-10-22', start: 's', end: 'e', session_count: 1 }], by_agent: [{ key: 'codex', session_count: 1, cost_share: 1 }], by_duration: [], by_cost: [] }
  const view = render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  fireEvent.click(view.container.querySelector('.session-bar')!)
  expect(state.bucket?.kind).toBe('time')
  state.dateRange = '24h'; view.rerender(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(state.bucket).toBeNull()
  fireEvent.click(view.container.querySelector('.session-stack button')!)
  expect(state.bucket?.kind).toBe('agent')
  state.activeSource = 'claude'; view.rerender(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(state.bucket).toBeNull()
  state.distribution = null
})
