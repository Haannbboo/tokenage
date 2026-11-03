// @vitest-environment jsdom
import { createElement } from 'react'
import { readFileSync } from 'node:fs'
import { cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { lanePositions, laneLayout, SessionLane } from '../src/components/SessionLane'
import { applySessionBucket, sessionFilterQuery, useSessionsData } from '../src/hooks/useSessionsData'
import { SessionDetail } from '../src/components/SessionDetail'
import type { SessionSummary, UsageRow } from '../src/types'
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ showToast: vi.fn() }) }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('places request starts proportionally to real time', () => {
  const rows = [{ id: 'a', ts: 1_000_000, latency_ms: 500 }, { id: 'b', ts: 3_601_000_000, latency_ms: null }] as UsageRow[]
  const real = lanePositions(rows)
  expect(real.marks[0].width).toBe(500)
  expect(real.marks[1].width).toBe(0)
  expect(real.marks[1].x).toBe(3_600_500)
})
it('maps dashboard and clicked time filters to API parameters', () => {
  const p = sessionFilterQuery('codex', 'device', { provider: 'openai', models: ['a', 'b'] }, 'old')
  applySessionBucket(p, { kind: 'time', key: '123', start: 'start', end: 'end' })
  expect(p.getAll('model')).toEqual(['a', 'b']); expect(p.get('provider')).toBe('openai')
  expect(p.get('since')).toBe('start'); expect(p.get('until')).toBe('end'); expect(p.has('key')).toBe(false)
  applySessionBucket(p, { kind: 'duration', key: '1-10m' }); expect(p.get('duration_bucket')).toBe('1-10m')
})
it('opens all session requests in logs from the latest 30', async () => {
  const rows = Array.from({ length: 30 }, (_, i) => ({ id: String(i), ts: 1_000_000 + i, model: 'test', latency_ms: null }))
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.startsWith('/usage?') ? rows : [] })))
  const navigate = vi.fn()
  const session = { session_id: 'abc', duration_s: 1, request_count: 45, total_tokens: 1, total_cost_usd: 1, model: 'test' } as SessionSummary
  render(createElement(SessionDetail, { session, onNavigateToLogs: navigate }))
  await waitFor(() => expect(screen.getByText('Latest 30 requests')).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: /View all in Request Logs/ }))
  expect(navigate).toHaveBeenCalledWith({ sessionFilter: 'abc' })
})

it('renders fixed-width accessible lane marks and formats session duration', async () => {
  const rows = [{ id: 'one', ts: 1_000_000, model: 'gpt-4', latency_ms: 800 }, { id: 'two', ts: 3_000_000, model: 'gpt-5', latency_ms: 500, status: 500 }] as UsageRow[]
  const select = vi.fn()
  const lane = render(createElement(SessionLane, { rows, selected: null, onSelect: select }))
  const marks = lane.container.querySelectorAll<HTMLButtonElement>('.session-mark')
  expect(marks).toHaveLength(2)
  expect(marks[0].parentElement?.style.width).not.toBe('')
  expect(marks[0].style.background).not.toBe(marks[1].style.background)
  expect(marks[1].classList.contains('error')).toBe(true)
  fireEvent.click(marks[0]); expect(select).toHaveBeenCalledWith('one')
  cleanup()
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => [] })))
  const session = { session_id: 'abc', duration_s: 848, request_count: 1, total_tokens: 1234, total_cost_usd: 1, model: 'test' } as SessionSummary
  render(createElement(SessionDetail, { session, onNavigateToLogs: vi.fn() }))
  expect(screen.getByText('14m 8s')).toBeTruthy()
  expect(screen.getByText('1,234')).toBeTruthy()
  await waitFor(() => expect(screen.getByText('No interactions found.')).toBeTruthy())
})

it('uses truthful axis times and separates scaled marks', () => {
  const rows = [{ id: 'a', ts: 1_000_000, latency_ms: 500 }, { id: 'b', ts: 1_000_000, latency_ms: 500 }, { id: 'c', ts: 3_601_000_000, latency_ms: 1000 }] as UsageRow[]
  const layout = laneLayout(rows)
  expect(layout.ticks).toHaveLength(5)
  expect(layout.ticks[0].time).toBe(500)
  expect(layout.ticks[4].time).toBe(3_601_000)
  expect(layout.marks[0].pxWidth).toBe(3)
  expect(layout.marks[1].px).toBeGreaterThanOrEqual(layout.marks[0].px + 4)
  expect(layout.marks[2].pxWidth).toBe(3)
})
it('links row and mark hover without scrolling', async () => {
  const rows = [{ id: 'a', ts: 1_000_000, model: 'model-a', latency_ms: 500 }]
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.startsWith('/usage?') ? rows : [] })))
  const view = render(createElement(SessionDetail, { session: { session_id: 'abc', duration_s: 1, request_count: 1, total_tokens: 1, total_cost_usd: 1, model: 'model-a' } as SessionSummary, onNavigateToLogs: vi.fn() }))
  await waitFor(() => expect(view.container.querySelector('.session-mark')).toBeTruthy())
  const row = view.container.querySelector('#session-request-a')!, mark = view.container.querySelector('.session-mark')!
  const style = document.createElement('style')
  style.textContent = readFileSync('src/App.css', 'utf8').replace('background: var(--surface-hover); }', 'background: rgb(12, 34, 56); }')
  document.head.appendChild(style)
  fireEvent.mouseEnter(row); expect(mark.classList.contains('selected')).toBe(true)
  fireEvent.mouseLeave(row); expect(mark.classList.contains('selected')).toBe(false)
  fireEvent.mouseEnter(mark); expect(row.classList.contains('session-row-hovered')).toBe(true)
  expect(getComputedStyle(row.querySelector('td')!).backgroundColor).toBe('rgb(12, 34, 56)')
  fireEvent.mouseLeave(mark); expect(row.classList.contains('session-row-hovered')).toBe(false)
  style.remove()
  fireEvent.keyDown(row, { key: ' ' }); expect(row.getAttribute('aria-expanded')).toBe('true')
})

it('formats unattributed tool timestamps in milliseconds', async () => {
  const ts = Date.UTC(2025, 9, 21, 12, 30)
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.startsWith('/usage?') ? [] : [{ tool_name: 'Search', tool_use_id: 'a', usage_id: null, ts }] })))
  render(createElement(SessionDetail, { session: { session_id: 'abc', duration_s: 1, request_count: 0, total_tokens: 0, total_cost_usd: 0 } as SessionSummary, tz: 'UTC', onNavigateToLogs: vi.fn() }))
  expect(await screen.findByText(/Search · Oct 21/)).toBeTruthy()
})

it('shows missing latency in the interaction and expanded detail', async () => {
  const rows = [{ id: 'missing', ts: 1_000_000, model: 'model-a', latency_ms: null }]
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.startsWith('/usage?') ? rows : [] })))
  const view = render(createElement(SessionDetail, { session: { session_id: 'abc', duration_s: 1, request_count: 1, total_tokens: 1, total_cost_usd: 1, model: 'model-a' } as SessionSummary, onNavigateToLogs: vi.fn() }))
  await waitFor(() => expect(view.container.querySelector('#session-request-missing')).toBeTruthy())
  const row = view.container.querySelector('#session-request-missing')!
  expect(row.children[4].textContent).toBe('—')
  fireEvent.click(row)
  await waitFor(() => expect([...view.container.querySelectorAll('.detail-group')].find(group => group.querySelector('.detail-label')?.textContent === 'Latency')?.querySelector('.detail-value')?.textContent).toBe('—'))
})

it('defaults to the latest session first', () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ sessions: [] }) })))
  const { result } = renderHook(() => useSessionsData({ activeSource: null, dateRange: 'all', bucket: null }))
  expect(result.current.sessionSortBy).toBe('ended')
  expect(result.current.sessionSortOrder).toBe('desc')
})
