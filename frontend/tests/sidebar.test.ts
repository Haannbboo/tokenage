// @vitest-environment jsdom
import { createElement } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../src/App'

function stubApi() {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname
    const data = path === '/auth/me' ? { provider: 'google', user: { id: 'u', email: 'a@example.com', name: 'Ada' } }
      : path === '/version' ? { name: 'tokenage', version: '9.9.9' }
      : path === '/sessions' ? { sessions: [], total: 0 }
      : path === '/sessions/distribution' ? { over_time: [], by_agent: [], by_duration: [], by_cost: [] }
      : []
    return { ok: true, status: 200, json: async () => data } as Response
  }))
}

function stubViewport(width: number) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: Number(/max-width: (\d+)px/.exec(query)?.[1] ?? 0) >= width,
    addEventListener() {}, removeEventListener() {},
  }))
}

beforeEach(() => { localStorage.clear(); stubApi() })
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const open = async (path: string) => {
  window.history.replaceState({}, '', path)
  render(createElement(App))
  return screen.findByRole('navigation', { name: 'Main' })
}

it('/sessions is its own page and the active sidebar item', async () => {
  await open('/sessions')
  expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Sessions')
  expect(screen.getByRole('link', { name: 'Sessions' }).getAttribute('aria-current')).toBe('page')
  expect(screen.getByRole('link', { name: 'Dashboard' }).getAttribute('aria-current')).toBeNull()
  // Sessions shows the filters its API honors (date, model, source, device); the Dashboard lost its sub-tabs.
  expect(screen.getByLabelText('Date Range')).toBeTruthy()
  expect(screen.getByLabelText('Source Filter')).toBeTruthy()
  expect(screen.getByLabelText('Device')).toBeTruthy()
  fireEvent.click(screen.getByRole('link', { name: 'Dashboard' }))
  expect((await screen.findByRole('heading', { level: 1 })).textContent).toBe('Dashboard')
  expect(window.location.pathname).toBe('/dashboard')
  expect(screen.queryByRole('button', { name: 'Sessions' })).toBeNull()
})

it('shows Projects as disabled, out of the tab order', async () => {
  await open('/dashboard')
  const projects = screen.getByRole('link', { name: 'Projects · Soon' })
  expect(projects.getAttribute('aria-disabled')).toBe('true')
  expect(projects.hasAttribute('href')).toBe(false)
  expect(projects.tabIndex).toBe(-1)
  expect(projects.textContent).toContain('Soon')
})

it('collapses to the icon rail and remembers it', async () => {
  await open('/dashboard')
  const app = document.querySelector('.app')!
  fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
  expect(app.classList.contains('is-collapsed')).toBe(true)
  expect(localStorage.getItem('tokenage-sidebar')).toBe('collapsed')
  cleanup()
  await open('/dashboard')
  expect(document.querySelector('.app')!.classList.contains('is-collapsed')).toBe(true)
  fireEvent.keyDown(document.body, { key: '[' })
  expect(document.querySelector('.app')!.classList.contains('is-collapsed')).toBe(false)
})

it('opens the account menu and closes it on Escape', async () => {
  await open('/dashboard')
  const trigger = screen.getByRole('button', { name: 'Account: Ada' })
  fireEvent.click(trigger)
  expect(screen.getByText('a@example.com')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByText('a@example.com')).toBeNull()
  expect(document.activeElement).toBe(trigger)
})

it('uses a drawer on phones: Escape, the scrim and navigation close it', async () => {
  stubViewport(400)
  await open('/logs')
  const sidebar = document.getElementById('sidebar')!
  expect(sidebar.hasAttribute('inert')).toBe(true)
  const menu = screen.getByRole('button', { name: 'Open menu' })
  const press = (el: Element) => act(async () => { fireEvent.click(el) })
  await press(menu)
  await waitFor(() => expect(sidebar.getAttribute('role')).toBe('dialog'))
  expect(sidebar.hasAttribute('inert')).toBe(false)
  expect(document.activeElement?.textContent).toBe('Dashboard')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(sidebar.getAttribute('role')).toBeNull()
  expect(document.activeElement).toBe(menu)

  await press(menu)
  fireEvent.pointerDown(document.querySelector('.scrim')!)
  expect(sidebar.getAttribute('role')).toBeNull()

  await press(menu)
  await press(screen.getByRole('link', { name: 'Sessions' }))
  expect(window.location.pathname).toBe('/sessions')
  expect(sidebar.getAttribute('role')).toBeNull()
})
