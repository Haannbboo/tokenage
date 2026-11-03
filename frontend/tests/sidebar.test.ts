// @vitest-environment jsdom
import { createElement } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from '../src/App'

function stubApi(provider = 'google', webSessions: { current: boolean }[] = []) {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('IntersectionObserver', class { observe() {} unobserve() {} disconnect() {} })
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname
    const data = path === '/auth/me' ? { provider, user: { id: 'u', email: 'a@example.com', name: 'Ada' } }
      : path === '/auth/devices' ? { devices: [], web_sessions: webSessions }
      : path === '/version' ? { name: 'tokenage', version: '9.9.9' }
      : path === '/sessions' ? { sessions: [], total: 0 }
      : path === '/sessions/distribution' ? { over_time: [], by_agent: [], by_duration: [], by_cost: [] }
      : []
    return { ok: true, status: 200, json: async () => data } as Response
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function stubViewport(width: number) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: Number(/max-width: (\d+)px/.exec(query)?.[1] ?? 0) >= width,
    addEventListener() {}, removeEventListener() {},
  }))
}

beforeEach(() => {
  localStorage.clear()
  window.history.replaceState({}, '', '/')
  stubViewport(1280)
  stubApi()
})
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
  await waitFor(() => expect(app.classList.contains('is-collapsed')).toBe(true))
  expect(localStorage.getItem('tokenage-sidebar')).toBe('collapsed')
  cleanup()
  await open('/dashboard')
  await waitFor(() => expect(document.querySelector('.app')!.classList.contains('is-collapsed')).toBe(true))
  fireEvent.keyDown(document.body, { key: '[' })
  await waitFor(() => expect(document.querySelector('.app')!.classList.contains('is-collapsed')).toBe(false))
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

it('offers Sign out to a local-auth browser signed in by login link', async () => {
  stubApi('local', [{ current: true }])
  await open('/dashboard')
  fireEvent.click(screen.getByRole('button', { name: 'Account: Ada' }))
  expect(await screen.findByRole('button', { name: 'Sign out' })).toBeTruthy()
})

it('hides Sign out for the local owner without a web session', async () => {
  const fetchMock = stubApi('local', [{ current: false }])
  await open('/dashboard')
  fireEvent.click(screen.getByRole('button', { name: 'Account: Ada' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith('/auth/devices'))).toBe(true))
  expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull()
})

it('Escape closes only the topmost layer: the account menu, then the drawer', async () => {
  stubViewport(400)
  await open('/logs')
  const sidebar = document.getElementById('sidebar')!
  const menu = screen.getByRole('button', { name: 'Open menu' })
  await act(async () => { fireEvent.click(menu) })
  await waitFor(() => expect(sidebar.getAttribute('role')).toBe('dialog'))
  const trigger = screen.getByRole('button', { name: 'Account: Ada' })
  fireEvent.click(trigger)
  expect(screen.getByText('a@example.com')).toBeTruthy()
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByText('a@example.com')).toBeNull()
  expect(sidebar.getAttribute('role')).toBe('dialog')
  expect(document.activeElement).toBe(trigger)
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(sidebar.getAttribute('role')).toBeNull()
  expect(document.activeElement).toBe(menu)
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
  await press(screen.getByRole('link', { name: 'Request Logs' }))
  expect(sidebar.getAttribute('role')).toBeNull()
  expect(document.activeElement).toBe(menu)

  await press(menu)
  await press(screen.getByRole('link', { name: 'Sessions' }))
  expect(window.location.pathname).toBe('/sessions')
  expect(sidebar.getAttribute('role')).toBeNull()
})

it('resets phone and tablet overlays when leaving their viewport', async () => {
  let width = 400
  const listeners = new Set<() => void>()
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: Number(/max-width: (\d+)px/.exec(query)?.[1] ?? 0) >= width,
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  }))
  await open('/dashboard')
  fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
  const resize = (next: number) => act(() => { width = next; listeners.forEach(listener => listener()) })
  resize(800)
  fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }))
  resize(1200)
  resize(800)
  expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeTruthy()
  resize(400)
  expect(document.getElementById('sidebar')!.hasAttribute('inert')).toBe(true)
})

it('closes the tablet overlay when clicking the current page link', async () => {
  stubViewport(800)
  await open('/dashboard')
  fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }))
  fireEvent.click(screen.getByRole('link', { name: 'Dashboard' }))
  expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeTruthy()
})

it('exposes settings sections as pressed buttons', async () => {
  await open('/settings')
  const pricing = screen.getByRole('button', { name: 'Pricing' })
  const services = screen.getByRole('button', { name: 'Services' })
  expect(services.getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(pricing)
  expect(pricing.getAttribute('aria-pressed')).toBe('true')
  expect(services.getAttribute('aria-pressed')).toBe('false')
  expect(screen.queryByRole('tablist')).toBeNull()
})
