// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SessionsTab } from '../src/pages/SessionsTab'

const state = vi.hoisted(() => ({ project: null as string | null, distributionFailed: false }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ activeSource: null, dateRange: 'all', timezone: 'UTC', showToast: vi.fn() }) }))
vi.mock('../src/hooks/useSessionsData', () => ({ useSessionsData: (opts: any) => {
  state.project = opts.project
  return { sessions: [{ session_id: 'unassigned-session', project: null, client_source: 'test', started: 1000000, ended: 2000000, request_count: 1, total_cost_usd: 0 }, { session_id: 'assigned-session', project: 'orchard', client_source: 'test', started: 1000000, ended: 2000000, request_count: 1, total_cost_usd: 0 }], sessionsLoading: false, distributionLoading: false, distribution: state.distributionFailed ? null : { by_agent: [], by_cost: [], by_duration: [], over_time: [], by_project: [{ key: 'orchard', session_count: 2, total_tokens: 1234, total_cost_usd: 1.5 }, { key: null, session_count: 1, total_tokens: 20, total_cost_usd: .1 }] } }
} }))
afterEach(() => { cleanup(); state.distributionFailed = false })
it('shows project totals and lets users filter and clear assigned and unassigned sessions', () => {
  render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(screen.getByText('1,234')).toBeTruthy()
  expect(screen.getByText('$1.50')).toBeTruthy()
  const select = screen.getByRole('combobox', { name: 'Project' })
  fireEvent.change(select, { target: { value: 'project:orchard' } })
  expect(state.project).toBe('orchard')
  fireEvent.click(screen.getByRole('button', { name: /^Unassigned / }))
  expect(state.project).toBe('')
  fireEvent.change(select, { target: { value: 'all' } })
  expect(state.project).toBe(null)
})

it('leaves unassigned session project cells empty while keeping the filter label', () => {
  const view = render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  const cells = view.container.querySelectorAll('.session-project-name')
  expect(cells[0].textContent).toBe('')
  expect(cells[1].textContent).toBe('orchard')
  expect(screen.getByRole('option', { name: 'Unassigned' })).toBeTruthy()
  expect(screen.getByRole('button', { name: /^Unassigned / })).toBeTruthy()
})

it('filters from any project row cell and clears on a second click', () => {
  render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  const row = screen.getByRole('button', { name: /^orchard / })
  expect(row.tagName).toBe('TR')
  expect(row.querySelector('button')).toBeNull()
  expect(row.getAttribute('tabindex')).toBe('0')
  expect(row.getAttribute('aria-pressed')).toBe('false')
  fireEvent.click(within(row).getByText('1,234'))
  expect(state.project).toBe('orchard')
  expect(row.getAttribute('aria-pressed')).toBe('true')
  expect(row.classList.contains('selected')).toBe(true)
  fireEvent.click(row)
  expect(state.project).toBeNull()
  expect(row.getAttribute('aria-pressed')).toBe('false')
  expect(row.classList.contains('selected')).toBe(false)
})

it('toggles assigned and unassigned project filters with Enter and Space', () => {
  render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  const select = screen.getByRole('combobox', { name: 'Project' }) as HTMLSelectElement
  for (const [name, project] of [['orchard', 'orchard'], ['Unassigned', '']]) {
    const row = screen.getByRole('button', { name: new RegExp(`^${name} `) })
    fireEvent.keyDown(row, { key: 'Enter' })
    expect(state.project).toBe(project)
    expect(select.value).toBe(`project:${project}`)
    expect(row.getAttribute('aria-pressed')).toBe('true')
    const key = new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true })
    fireEvent(row, key)
    expect(key.defaultPrevented).toBe(true)
    expect(state.project).toBeNull()
    expect(select.value).toBe('all')
    expect(row.getAttribute('aria-pressed')).toBe('false')
  }
})

it('keeps a clear chip for the project filter when distributions fail', () => {
  const view = render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  fireEvent.change(screen.getByRole('combobox', { name: 'Project' }), { target: { value: 'project:orchard' } })
  state.distributionFailed = true
  view.rerender(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(screen.queryByRole('combobox', { name: 'Project' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Project: orchard ×' }))
  expect(state.project).toBeNull()
  expect(screen.queryByRole('button', { name: /^Project: / })).toBeNull()
})
