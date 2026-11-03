// @vitest-environment jsdom
import { createElement } from 'react'
import { readFileSync } from 'node:fs'
import { cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { lanePositions, laneLayout, SessionLane } from '../src/components/SessionLane'
import { applySessionBucket, sessionFilterQuery, useSessionsData } from '../src/hooks/useSessionsData'
import { SessionContextChart } from '../src/components/SessionContextChart'
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
  style.textContent = readFileSync('src/App.css', 'utf8').replace('tr.session-row-hovered > td { background: var(--surface-hover); }', 'tr.session-row-hovered > td { background: rgb(12, 34, 56); }')
  document.head.appendChild(style)
  fireEvent.mouseEnter(row); expect(mark.classList.contains('selected')).toBe(true)
  expect(view.container.querySelector('.session-lane-track')!.classList.contains('has-hover')).toBe(true)
  fireEvent.mouseLeave(row); expect(mark.classList.contains('selected')).toBe(false)
  fireEvent.mouseEnter(mark); expect(row.classList.contains('session-row-hovered')).toBe(true)
  expect(getComputedStyle(row.querySelector('td')!).backgroundColor).toBe('rgb(12, 34, 56)')
  fireEvent.mouseLeave(mark); expect(row.classList.contains('session-row-hovered')).toBe(false)
  style.remove()
  fireEvent.keyDown(row, { key: ' ' }); expect(row.getAttribute('aria-expanded')).toBe('true')
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


it('draws 46 requests as one continuous context path', () => {
  const points = Array.from({ length: 46 }, (_, i) => ({ request: i + 1, ts: 1000 + i, context_tokens: i === 20 ? 439000 : i * 100 }))
  const data = { points, peak_tokens: 439000, request_count: 46 }
  const view = render(createElement(SessionContextChart, { data }))
  expect(screen.getByText('Peak 439k')).toBeTruthy()
  expect(screen.getByText('600k')).toBeTruthy()
  const paths = view.container.querySelectorAll('path[stroke]')
  expect(paths).toHaveLength(1)
  expect(paths[0].getAttribute('d')?.match(/L/g)).toHaveLength(45)
  view.rerender(createElement(SessionContextChart, { data: { ...data, points: points.slice(0, 1) } }))
  expect(view.container.querySelector('svg')).toBeNull()
  view.rerender(createElement(SessionContextChart, { data: { points: points.map(p => ({ ...p, context_tokens: 0 })), peak_tokens: 0, request_count: 46 } }))
  expect(view.container.querySelector('svg')).toBeNull()
})

it('fetches context for the expanded session', async () => {
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/context') ? { points: [], peak_tokens: 0 } : [] }))
  vi.stubGlobal('fetch', fetchMock)
  render(createElement(SessionDetail, { session: { session_id: 'a/b', duration_s: 1, request_count: 0, total_tokens: 0, total_cost_usd: 0 } as SessionSummary, onNavigateToLogs: vi.fn() }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/sessions/a%2Fb/context', expect.objectContaining({ signal: expect.any(AbortSignal) })))
})

it('links hover across the context chart, timeline and request rows by request id', async () => {
  const context = { points: ['a', 'b', 'c'].map((id, i) => ({ request: i + 1, id, ts: 1_000_000 * (i + 1), model: 'm', cost_usd: 0.01, latency_ms: 500, context_tokens: (i + 1) * 1000 })), peak_tokens: 3000, request_count: 3 }
  // Newest first, as /usage returns them: the order differs from the chart's.
  const rows = ['c', 'b', 'a'].map((id, i) => ({ id, ts: 1_000_000 * (3 - i), model: 'm', status: 200, latency_ms: 500 }))
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/context') ? context : rows })))
  const view = render(createElement(SessionDetail, { session: { session_id: 's', duration_s: 1, request_count: 3, total_tokens: 0, total_cost_usd: 0 } as SessionSummary, onNavigateToLogs: vi.fn() }))
  await waitFor(() => expect(document.getElementById('session-request-c')).toBeTruthy())
  const svg = await waitFor(() => { const el = view.container.querySelector<SVGSVGElement>('.session-context svg'); expect(el).toBeTruthy(); return el! })
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 200, right: 800, bottom: 200, x: 0, y: 0, toJSON() {} })

  // Chart → row and timeline: the last point is request c.
  fireEvent.mouseMove(svg, { clientX: 790 })
  expect(document.getElementById('session-request-c')!.className).toContain('session-row-hovered')
  expect(view.container.querySelectorAll('.session-mark.selected')).toHaveLength(1)
  expect(screen.getByText('Request 3', { selector: '.chart-tip-title' })).toBeTruthy()
  fireEvent.mouseLeave(svg)
  expect(view.container.querySelector('.session-row-hovered')).toBeNull()
  expect(view.container.querySelector('.session-context-marker')).toBeNull()

  // Row → chart: the marker sits on request a's point, without a tooltip.
  fireEvent.mouseEnter(document.getElementById('session-request-a')!)
  const marker = view.container.querySelector('.session-context-marker line')!
  expect(marker.getAttribute('x1')).toBe('48')
  expect(view.container.querySelector('.chart-tip')).toBeNull()
})

it('shades only the part of the chart the timeline and list cover', () => {
  const points = Array.from({ length: 10 }, (_, i) => ({ request: i + 1, id: `r${i}`, ts: 1000 * (i + 1), model: 'm', cost_usd: 0, latency_ms: null, context_tokens: (i + 1) * 100 }))
  const data = { points, peak_tokens: 1000, request_count: 10 }
  const props = { data, hoveredId: null, onHover: vi.fn(), colorOf: () => 'red' }
  const view = render(createElement(SessionContextChart, { ...props, linkedRange: { from: 8000, to: 10000 } }))
  const shade = view.container.querySelector('.session-context-linked')!
  // Requests 8-10 of 10 on an 800-wide plot (x from 48 to 784), padded 3px each side.
  expect(Number(shade.getAttribute('x'))).toBeCloseTo(48 + 7 / 9 * 736 - 3)
  view.rerender(createElement(SessionContextChart, { ...props, linkedRange: null }))
  expect(view.container.querySelector('.session-context-linked')).toBeNull()
})

it('updates the chart tooltip cost after a cost recalculation', async () => {
  const context = { points: ['a', 'b'].map((id, i) => ({ request: i + 1, id, ts: 1_000_000 * (i + 1), model: 'm', cost_usd: 0.01, latency_ms: null, context_tokens: (i + 1) * 1000 })), peak_tokens: 2000, request_count: 2 }
  const rows = ['b', 'a'].map((id, i) => ({ id, ts: 1_000_000 * (2 - i), model: 'm', status: 200, total_cost_usd: 0.01 }))
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => ({ ok: true, json: async () =>
    init?.method === 'POST' ? { new_costs: { input_cost_usd: 0.5, output_cost_usd: 0.25, total_cost_usd: 0.75 } }
      : url.endsWith('/context') ? context : rows })))
  const view = render(createElement(SessionDetail, { session: { session_id: 's', duration_s: 1, request_count: 2, total_tokens: 0, total_cost_usd: 0 } as SessionSummary, onNavigateToLogs: vi.fn() }))
  await waitFor(() => expect(document.getElementById('session-request-b')).toBeTruthy())
  const svg = view.container.querySelector<SVGSVGElement>('.session-context svg')!
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 800, height: 200, right: 800, bottom: 200, x: 0, y: 0, toJSON() {} })
  fireEvent.click(document.getElementById('session-request-b')!.querySelector('button[aria-label="Click to recalculate"]')!)
  await waitFor(() => expect(document.getElementById('session-request-b')!.textContent).toContain('$0.75'))
  fireEvent.mouseMove(svg, { clientX: 790 })
  expect(view.container.querySelector('.chart-tip')!.textContent).toContain('$0.75')
})
