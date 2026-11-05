// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { SessionsTab } from '../src/pages/SessionsTab'

const state = vi.hoisted(() => ({ project: null as string | null }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ activeSource: null, dateRange: 'all', timezone: 'UTC', showToast: vi.fn() }) }))
vi.mock('../src/hooks/useSessionsData', () => ({ useSessionsData: (opts: any) => {
  state.project = opts.project
  return { sessions: [], sessionsLoading: false, distributionLoading: false, distribution: { by_agent: [], by_cost: [], by_duration: [], over_time: [], by_project: [{ key: 'orchard', session_count: 2, total_tokens: 1234, total_cost_usd: 1.5 }, { key: null, session_count: 1, total_tokens: 20, total_cost_usd: .1 }] } }
} }))
afterEach(cleanup)
it('shows project totals and lets users filter and clear assigned and unassigned sessions', () => {
  render(createElement(SessionsTab, { onNavigateToLogs: vi.fn() }))
  expect(screen.getByText('1,234')).toBeTruthy()
  expect(screen.getByText('$1.50')).toBeTruthy()
  const select = screen.getByRole('combobox', { name: 'Project' })
  fireEvent.change(select, { target: { value: 'project:orchard' } })
  expect(state.project).toBe('orchard')
  fireEvent.click(screen.getByRole('button', { name: 'Unassigned' }))
  expect(state.project).toBe('')
  fireEvent.change(select, { target: { value: 'all' } })
  expect(state.project).toBe(null)
})
