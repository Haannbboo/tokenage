// @vitest-environment jsdom
// @vitest-environment-options {"url":"http://10.0.0.5:4001"}
import { createElement } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../src/App'

const ME = { provider: 'google', user: { id: 'u', email: 'a@example.com' } }

/** An account with nothing tracked yet. */
function stubApi(version: Record<string, unknown>) {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(String(input), window.location.origin)
    const reply = url.pathname === '/auth/me' ? ME
      : url.pathname === '/version' ? { version: '1', ...version }
      : url.pathname === '/usage/count' ? { total: 0 }
      : []
    return { ok: true, status: 200, json: async () => reply } as Response
  }))
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear() })

const open = () => {
  window.history.replaceState({}, '', '/dashboard')
  render(createElement(App))
}

it('tells a new user to install the client from the configured server, not the browser origin', async () => {
  stubApi({ server_url: 'https://tokens.example.com', installer_available: true })
  open()
  expect(await screen.findByText('curl -fsSL https://tokens.example.com/install.sh | sh')).toBeTruthy()
  expect(screen.getByText('No usage tracked yet')).toBeTruthy()
  expect(screen.getByText('Waiting for your first request. This page checks automatically.')).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Self-hosting with Docker' }).getAttribute('href'))
    .toBe('https://github.com/Haannbboo/tokenage/blob/main/docker/README.md')
  expect(document.body.textContent).not.toContain('tokenage bootstrap')
  expect(document.body.textContent).not.toContain('tokenage claude')
})

it('explains that an HTTP self-host needs HTTPS', async () => {
  stubApi({ server_url: null, installer_available: false })
  open()
  expect(await screen.findByText(/This server needs HTTPS before clients can connect/)).toBeTruthy()
  expect(screen.queryByText('This server cannot serve the installer. When it asks for the server URL, enter:')).toBeNull()
  expect(screen.queryByText(/curl -fsSL/)).toBeNull()
  expect(screen.queryByText('tokenage update')).toBeNull()
  for (const link of screen.getAllByRole('link', { name: 'Self-hosting with Docker' })) {
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toBe('noreferrer')
  }
})


it('shows a URL hint for an HTTPS server without an installer', async () => {
  stubApi({ server_url: 'https://tokens.example.com', installer_available: false })
  open()
  expect(await screen.findByText('https://tokens.example.com')).toBeTruthy()
  expect(screen.getByText('This server cannot serve the installer. When it asks for the server URL, enter:')).toBeTruthy()
  expect(screen.queryByText('This server needs HTTPS before clients can connect')).toBeNull()
})

it('does not flash an install command before version arrives', async () => {
  stubApi({})
  const fetchOther = globalThis.fetch
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    String(input) === '/version' ? new Promise<Response>(() => {}) : fetchOther(input, init)))
  open()
  await screen.findByText('No usage tracked yet')
  expect(screen.queryByText(/curl -fsSL/)).toBeNull()
})

it('keeps one live region when the first request arrives and labels token counts', async () => {
  const { EmptyGuide } = await import('../src/components/EmptyGuide')
  stubApi({ server_url: 'https://tokens.example.com', installer_available: true })
  const props = { verificationResult: null, onViewLogs: vi.fn(), onDone: vi.fn() }
  const { rerender } = render(createElement(EmptyGuide, props))
  const live = screen.getByRole('status')
  const result = { client_source: 'claude-code', model: 'test-model', prompt_tokens: 1234, completion_tokens: 56 } as import('../src/types').UsageRow
  rerender(createElement(EmptyGuide, { ...props, verificationResult: result }))
  expect(screen.getByRole('status')).toBe(live)
  expect(live.textContent).toContain('Tracking works.')
  expect(live.textContent).toContain('Claude Code')
  expect(live.textContent).not.toContain('claude-code')
  expect(live.textContent).toContain('In: 1,234 / Out: 56')
  expect(screen.getByText('Kilo Code')).toBeTruthy()
})

it('renders when the server origin cannot be parsed', async () => {
  const { EmptyGuide } = await import('../src/components/EmptyGuide')
  stubApi({ server_url: 'null', installer_available: false })
  render(createElement(EmptyGuide, { verificationResult: null, onViewLogs: vi.fn(), onDone: vi.fn() }))
  expect(await screen.findByText(/curl -fsSL https:\/\/raw.githubusercontent.com/)).toBeTruthy()
  expect(screen.queryByText(/When it asks for the server URL/)).toBeNull()
})
