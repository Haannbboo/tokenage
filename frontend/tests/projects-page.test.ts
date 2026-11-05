// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ProjectsPage } from '../src/pages/ProjectsPage'

const state = vi.hoisted(() => ({ data: null as any, detail: null as any, loading: false, error: false, detailError: false, selected: null as string | null }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ timezone: 'UTC' }) }))
vi.mock('../src/pages/SessionsPage', () => ({ SessionPageHeader: () => createElement('h1', null, 'Projects') }))
vi.mock('../src/hooks/useProjectsData', () => ({ useProjectsData: (project: string | null) => {
  state.selected = project
  return { list: { data: state.data, loading: state.loading, error: state.error }, detail: { data: state.detail, loading: false, error: state.detailError } }
} }))
afterEach(() => { cleanup(); state.loading = false; state.error = false; state.detailError = false; state.data = null })
function fixture() {
  const project = { key: 'orchard', session_count: 2, total_cost_usd: 6, request_count: 4, prompt_tokens: 60, cached_tokens: 40, cache_creation_tokens: 20, last_active: 2000000, agents: { codex: 1, opencode: 1 } }
  state.data = { projects: [project, { ...project, key: 'grove' }], over_time: [{ key: 1, period: '2026-04-20', start: '2026-04-20', end: '2026-04-21', by_project: { orchard: 2, grove: 1 } }] }
  state.detail = { key: project.key, failed_requests: 1, first_started: 1000000, latency_sum_ms: 8000, models: { 'model-a': 6 }, tools: { Read: 4 }, recent_sessions: [{ session_id: 'session-one', client_source: 'codex', ended: 2000000, request_count: 4, total_cost_usd: 6 }] }
}
function open(path = '/projects') {
  return render(createElement(MemoryRouter, { initialEntries: [path] }, createElement(Routes, null,
    createElement(Route, { path: '/projects', element: createElement(ProjectsPage) }),
    createElement(Route, { path: '/sessions', element: createElement('p', null, 'Sessions destination') }))))
}
it('renders project metrics without failed column or unassigned totals', () => {
  fixture()
  const view = open()
  expect(screen.getByText('Sessions per day, by project')).toBeTruthy()
  expect(view.container.querySelectorAll('.project-stacked-bar i')).toHaveLength(2)
  const heights = [...view.container.querySelectorAll<HTMLElement>('.project-stacked-bar i')].map(el => parseFloat(el.style.height))
  expect(heights[0]).toBeCloseTo(2 / 3 * 100)
  expect(heights[1]).toBeCloseTo(1 / 3 * 100)
  expect(screen.getAllByText('50.0%')).toHaveLength(2)
  expect(screen.queryByRole('columnheader', { name: 'Failed' })).toBeNull()
  expect(screen.queryByText(/Without a project:/)).toBeNull()
  expect(view.container.querySelectorAll('.project-agent-mix i')).toHaveLength(4)
})
it('toggles the URL drawer with click, Enter and Space, and restores focus after switching rows', () => {
  fixture()
  const view = open()
  const rows = view.container.querySelectorAll<HTMLElement>('.projects-table tbody tr')
  const colorsBefore = [...view.container.querySelectorAll<HTMLElement>('.project-legend i')].map(el => el.style.background)
  fireEvent.click(rows[0])
  expect([...view.container.querySelectorAll<HTMLElement>('.project-legend i')].map(el => el.style.background)).toEqual(colorsBefore)
  expect(state.selected).toBe('orchard')
  expect((view.container.querySelector('.project-legend i') as HTMLElement).style.background).toBe((view.container.querySelector('.project-stacked-bar i') as HTMLElement).style.background)
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(view.container.querySelectorAll('.project-tiles > div')).toHaveLength(8)
  expect(screen.getByText('2.0 s')).toBeTruthy()
  expect(screen.getByText('Failed requests')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Open in Sessions →' }).closest('.detail-drawer-actions')?.contains(screen.getByRole('button', { name: 'Close' }))).toBe(true)
  fireEvent.click(rows[0])
  expect(screen.queryByRole('dialog')).toBeNull()
  rows[0].focus()
  fireEvent.keyDown(rows[0], { key: 'Enter' })
  fireEvent.click(rows[1])
  expect(state.selected).toBe('grove')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(document.activeElement).toBe(rows[1])
  fireEvent.keyDown(rows[1], { key: ' ' })
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(state.selected).toBeNull()
})
it('supports project deep links and recent session navigation', async () => {
  fixture()
  const view = open('/projects?project=orchard')
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Open in Sessions →' }).getAttribute('href')).toBe('/sessions?project=orchard&range=30d')
  fireEvent.click(view.container.querySelector('.project-recent-table tbody tr')!)
  await waitFor(() => expect(screen.getByText('Sessions destination')).toBeTruthy())
  expect(screen.queryByRole('dialog')).toBeNull()
})
it('shows loading, error and empty states', () => {
  state.loading = true
  const view = open()
  expect(screen.getByText('Loading...')).toBeTruthy()
  state.loading = false; state.error = true
  view.rerender(createElement(MemoryRouter, null, createElement(ProjectsPage)))
  expect(screen.getByRole('alert')).toBeTruthy()
  state.error = false; state.data = { projects: [], over_time: [] }
  view.rerender(createElement(MemoryRouter, null, createElement(ProjectsPage)))
  expect(screen.getByText('No projects yet.')).toBeTruthy()
})
it('treats an empty project param as no drawer', () => {
  fixture()
  open('/projects?project=')
  expect(state.selected).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
})
it('merges detail extras with the list row and uses the trimmed chart activity for tiles', () => {
  fixture()
  state.data.over_time = Array.from({ length: 31 }, (_, i) => ({ key: i, period: '2026-04-20', start: '2026-04-20', end: '2026-04-21', by_project: i === 0 || i === 30 ? { orchard: 1 } : {} }))
  const view = open('/projects?project=orchard')
  expect(view.container.querySelectorAll('.project-stacked-bar')).toHaveLength(30)
  expect(screen.getByText('1 / 30')).toBeTruthy()
  expect(screen.getByText(/Active 1 of 30 days/)).toBeTruthy()
  expect(screen.getByText('Failed requests')).toBeTruthy()
})
it('keeps the table and drawer mounted during refresh', () => {
  fixture()
  state.loading = true
  const view = open('/projects?project=orchard')
  expect(view.container.querySelector('.projects-table')).toBeTruthy()
  expect(view.container.querySelector('.project-tiles')).toBeTruthy()
  expect(screen.queryByText('Loading...')).toBeNull()
})
