// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SessionsTab } from '../src/pages/SessionsTab'

const state = vi.hoisted(() => ({ project: null as string | null }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ activeSource: null, dateRange: 'all', timezone: 'UTC', showToast: vi.fn() }) }))
vi.mock('../src/hooks/useSessionsData', () => ({ useSessionsData: (opts: any) => {
  state.project = opts.project
  return { sessions: [{ session_id: 'unassigned-session', project: null, client_source: 'test', started: 1000000, ended: 2000000, request_count: 1, total_cost_usd: 0 }, { session_id: 'assigned-session', project: 'orchard', client_source: 'test', started: 1000000, ended: 2000000, request_count: 1, total_cost_usd: 0 }], sessionsLoading: false, distributionLoading: false, distribution: { by_agent: [], by_cost: [], by_duration: [], over_time: [] } }
} }))
vi.mock('../src/components/SessionDetail', () => ({ SessionDetail: () => createElement('p', null, 'session details') }))
function Location() { return createElement('output', { 'data-testid': 'location' }, useLocation().search) }
function open(url = '/sessions') {
  return render(createElement(MemoryRouter, { initialEntries: [url] }, createElement(SessionsTab, { onNavigateToLogs: vi.fn() }), createElement(Location)))
}
afterEach(cleanup)
it('takes the project filter from the URL and clears it with a chip', () => {
  open('/sessions?project=orchard')
  expect(state.project).toBe('orchard')
  expect(screen.queryByRole('combobox', { name: 'Project' })).toBeNull()
  expect(screen.queryByText('By project')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Project: orchard ×' }))
  expect(state.project).toBeNull()
  expect(screen.getByTestId('location').textContent).toBe('')
})
it('keeps unassigned project cells empty and allows clearing an unassigned URL filter', () => {
  const view = open('/sessions?project=')
  const cells = view.container.querySelectorAll('.session-project-name')
  expect(cells[0].textContent).toBe('')
  expect(cells[1].textContent).toBe('orchard')
  expect(state.project).toBe('')
  fireEvent.click(screen.getByRole('button', { name: 'Project: Unassigned ×' }))
  expect(state.project).toBeNull()
})
it('opens a deep-linked session in a drawer and returns focus to its row on Escape', () => {
  const view = open('/sessions?project=orchard')
  const row = view.container.querySelectorAll<HTMLElement>('tr.session-drawer-row')[1]
  row.focus()
  fireEvent.keyDown(row, { key: ' ' })
  expect(screen.getByRole('dialog').getAttribute('aria-labelledby')).toBeTruthy()
  expect(screen.getByText('session details')).toBeTruthy()
  expect(screen.getByTestId('location').textContent).toBe('?project=orchard&session=assigned-session')
  expect(row.classList.contains('selected')).toBe(true)
  expect(row.parentElement?.querySelectorAll('tr')).toHaveLength(2)
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.activeElement).toBe(row)
  expect(screen.getByTestId('location').textContent).toBe('?project=orchard')
})
it('resolves session deep links and drops IDs absent from the loaded list', () => {
  open('/sessions?project=orchard&session=assigned-session')
  expect(screen.getByRole('dialog')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(screen.getByTestId('location').textContent).toBe('?project=orchard')
  cleanup()
  open('/sessions?project=orchard&session=missing')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByTestId('location').textContent).toBe('?project=orchard')
})
it('uses dialog-trigger semantics and omits empty project metadata', () => {
  const view = open('/sessions?session=unassigned-session')
  const row = view.container.querySelector('.session-drawer-row')!
  expect(row.getAttribute('aria-haspopup')).toBe('dialog')
  expect(row.getAttribute('aria-pressed')).toBe('true')
  expect(row.hasAttribute('aria-expanded')).toBe(false)
  expect(view.container.querySelector('.detail-drawer-meta')?.textContent).not.toContain(' ·  · ')
})
