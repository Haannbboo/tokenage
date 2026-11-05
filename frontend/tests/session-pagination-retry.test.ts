// @vitest-environment jsdom
import { createElement, type ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { cleanup, fireEvent, render as renderInRouter, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SessionsTab } from '../src/pages/SessionsTab'
const render = (ui: ReactElement) => renderInRouter(ui, { wrapper: MemoryRouter })

vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ refreshTrigger: 0, activeDevice: null, activeFilter: null, activeSource: null, dateRange: 'all', timezone: 'UTC', showToast: vi.fn() }) }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('keeps rows and retries the failed page when loading more', async () => {
  const offsets: string[] = []
  const page = (start: number, count: number) => Array.from({ length: count }, (_, i) => ({ session_id: `session-${start + i}`, started: 1000000, ended: 2000000, client_source: 'test', request_count: 1, total_cost_usd: 0 }))
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = new URL(input, window.location.origin)
    if (url.pathname !== '/sessions') return { ok: true, json: async () => null }
    const offset = url.searchParams.get('offset')!
    offsets.push(offset)
    if (offsets.length === 2) return { ok: false }
    return { ok: true, json: async () => ({ sessions: offset === '0' ? page(0, 50) : page(50, 1) }) }
  }))
  const view = render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  await waitFor(() => expect(view.container.querySelectorAll('tr.session-drawer-row')).toHaveLength(50))
  fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  await screen.findByRole('alert')
  expect(view.container.querySelectorAll('tr.session-drawer-row')).toHaveLength(50)
  fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  await waitFor(() => expect(view.container.querySelectorAll('tr.session-drawer-row')).toHaveLength(51))
  expect(offsets).toEqual(['0', '50', '50'])
  expect(screen.queryByRole('alert')).toBeNull()
})
