// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SessionsTab } from '../src/pages/SessionsTab'
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ showToast: vi.fn(), dateRange: 'all' }) }))
vi.mock('../src/hooks/useSessionsData', () => ({ useSessionsData: () => ({ sessions: [{ session_id: 'session-123456789', started: 1000000, ended: 2000000, client_source: 'test', request_count: 1, total_cost_usd: 0 }], distribution: null, sessionsLoading: false, sessionsError: false, hasMoreSessions: false, sessionSortBy: 'started', sessionSortOrder: 'asc', handleSessionSort: vi.fn(), setSessionPage: vi.fn() }) }))
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
