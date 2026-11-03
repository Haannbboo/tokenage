// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../src/App'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('shows machines and web sessions separately with no revoke for this session', async () => {
  window.history.replaceState({}, '', '/settings?section=devices')
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname
    const data = path === '/auth/me' ? { provider: 'google', user: { id: 'u', email: 'a@example.com' } }
      : path === '/auth/devices' ? {
        devices: [{ id: 'm', kind: 'client', device_name: 'Laptop', created_at: 1, last_used_at: null, current: false }],
        web_sessions: [{ id: 'w1', device_name: 'Chrome on macOS', created_at: 1, last_used_at: null, current: true }, { id: 'w2', device_name: 'Firefox on Linux', created_at: 1, last_used_at: null, current: false }],
      } : []
    return { ok: true, status: 200, json: async () => data } as Response
  }))
  render(createElement(App))
  const machine = await screen.findByText('Laptop')
  const current = await screen.findByText('Chrome on macOS')
  expect(screen.getByText('Clients')).toBeTruthy()
  expect(screen.getByText('Web sessions')).toBeTruthy()
  expect(machine.closest('table')).not.toBe(current.closest('table'))
  expect(within(current.closest('tr')!).getByText('This session')).toBeTruthy()
  expect(within(current.closest('tr')!).queryByRole('button', { name: 'Revoke' })).toBeNull()
  expect(within(screen.getByText('Firefox on Linux').closest('tr')!).getByRole('button', { name: 'Revoke' })).toBeTruthy()
})
