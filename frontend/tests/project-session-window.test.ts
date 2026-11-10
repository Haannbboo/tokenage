// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../src/App'

vi.mock('../src/components/SessionDetail', () => ({ SessionDetail: () => createElement('p', null, 'Older session detail') }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('carries the Projects window into Sessions and opens a session older than 24 hours', async () => {
  const ended = (Date.now() - 3 * 86400_000) * 1000
  const project = { key: 'orchard', session_count: 1, total_cost_usd: 6, request_count: 4, prompt_tokens: 60, cached_tokens: 40, cache_creation_tokens: 20, last_active: ended, agents: { codex: 1 } }
  const session = { session_id: 'older-session', project: 'orchard', client_source: 'codex', started: ended - 1000000, ended, request_count: 4, total_cost_usd: 6 }
  const requests: URL[] = []
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = new URL(input, window.location.origin)
    requests.push(url)
    const data = url.pathname === '/auth/me' ? { provider: 'google', user: { id: 'u', email: 'u@example.com' } }
      : url.pathname === '/sessions/projects' ? { projects: [project], over_time: [] }
      : url.pathname === '/sessions/projects/detail' ? { key: project.key, failed_requests: 0, first_started: session.started, latency_sum_ms: 1000, models: {}, tools: {}, recent_sessions: [session], effort_mix: [] }
      : url.pathname === '/sessions' ? { sessions: Date.parse(url.searchParams.get('since')!) * 1000 <= ended ? [session] : [], total: 1 }
      : url.pathname === '/sessions/distribution' ? { over_time: [], by_agent: [], by_cost: [], by_duration: [] }
      : []
    return { ok: true, status: 200, json: async () => data }
  }))
  window.history.replaceState({}, '', '/projects')
  render(createElement(App))
  fireEvent.click(await screen.findByRole('button', { name: /^orchard / }))
  const link = await screen.findByRole('link', { name: 'Open in Sessions →' })
  expect(link.getAttribute('href')).toBe('/sessions?project=orchard&range=30d')
  const recent = await waitFor(() => {
    const row = document.querySelector('.project-recent-table tbody tr')
    expect(row).toBeTruthy()
    return row!
  })
  fireEvent.click(recent)
  await screen.findByText('Older session detail')
  expect(screen.getByRole('dialog').textContent).toContain('older-session')
  expect((screen.getByLabelText('Date Range') as HTMLSelectElement).value).toBe('30d')
  const search = new URLSearchParams(window.location.search)
  expect(search.has('range')).toBe(false)
  expect(search.get('session')).toBe('older-session')
  expect(search.get('project')).toBe('orchard')
  expect(requests.filter(url => url.pathname === '/sessions').every(url => Date.parse(url.searchParams.get('since')!) * 1000 <= ended)).toBe(true)
  expect(requests.find(url => url.pathname === '/sessions/distribution')?.searchParams.get('project')).toBe('orchard')
})
