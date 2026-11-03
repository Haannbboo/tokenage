// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SessionDetail } from '../src/components/SessionDetail'

vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ showToast: vi.fn() }) }))
vi.mock('../src/components/SessionContextChart', () => ({ SessionContextChart: () => createElement('div', null, 'context chart') }))
vi.mock('../src/components/SessionLane', () => ({ SessionLane: () => null }))
vi.mock('../src/components/RequestCell', () => ({ renderRequestCell: (row: any, column: string) => createElement('td', null, column === 'model' ? row.model : '') }))

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('recovers from a failed usage fetch and retains data during a same-session refresh', async () => {
  let usageCalls = 0
  let contextCalls = 0
  const pending = new Promise<Response>(() => {})
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    if (url.startsWith('/sessions/')) {
      contextCalls++
      return contextCalls === 1 ? Promise.resolve({ ok: true, json: async () => ({ peak_tokens: 1234 }) }) : pending
    }
    usageCalls++
    if (usageCalls === 1) return Promise.resolve({ ok: false })
    if (usageCalls === 2) return Promise.resolve({ ok: true, json: async () => [{ id: 'row-1', model: 'visible model' }] })
    return pending
  }))
  const session = { session_id: 'one', request_count: 1, ended: 1, duration_s: 1, total_tokens: 1, total_cost_usd: 0 } as any
  const props = { session, onNavigateToLogs: vi.fn() }
  const view = render(createElement(SessionDetail, props))
  await screen.findByRole('alert')
  await screen.findByText('context chart')
  view.rerender(createElement(SessionDetail, { ...props, session: { ...session, request_count: 2 } }))
  await screen.findByText('visible model')
  expect(screen.queryByRole('alert')).toBeNull()
  expect(screen.getByText('context chart')).toBeTruthy()
  expect(screen.getByText('1,234')).toBeTruthy()
  view.rerender(createElement(SessionDetail, { ...props, session: { ...session, request_count: 3 } }))
  await waitFor(() => expect(usageCalls).toBe(3))
  expect(screen.getByText('visible model')).toBeTruthy()
  expect(screen.getByText('context chart')).toBeTruthy()
  expect(screen.queryByText('Loading...')).toBeNull()
})
