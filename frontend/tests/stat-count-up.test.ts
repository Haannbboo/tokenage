// @vitest-environment jsdom
import { createElement } from 'react'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../src/App'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('shows whole requests and tokens while the KPIs count up', async () => {
  const row = { provider: 'p', model: 'm', requests: 7, prompt_tokens: 400, completion_tokens: 123, cached_tokens: 0, cache_creation_tokens: 0, total_tokens: 523, total_cost_usd: 1.5, latency_sum_ms: 1000 }
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname
    const data = path === '/auth/me' ? { provider: 'google', user: { id: 'u', email: 'a@example.com' } }
      : path === '/usage/summary' ? [row] : path === '/usage/count' ? { total: 7 } : []
    return { ok: true, status: 200, json: async () => data } as Response
  }))
  // Hold the count-up mid-way: frames run only when the test steps them.
  const frames: FrameRequestCallback[] = []
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb))
  vi.stubGlobal('cancelAnimationFrame', () => {})
  window.history.replaceState({}, '', '/dashboard')
  render(createElement(App))
  await screen.findByText('Requests', { selector: '.stat-label' })
  await act(async () => { frames.splice(0).forEach(cb => cb(0)) })
  await act(async () => { frames.splice(0).forEach(cb => cb(100)) })
  const value = (label: string) => screen.getByText(label, { selector: '.stat-label' }).nextElementSibling!.textContent!
  expect(value('Requests')).toMatch(/^\d+$/)
  expect(Number(value('Requests'))).toBeGreaterThan(0)
  expect(Number(value('Requests'))).toBeLessThan(7)
  expect(value('Token Usage')).toMatch(/^\d+$/)
  // Cost keeps its cents mid-way.
  expect(value('Estimated Cost')).toMatch(/^\$\d+\.\d{2}$/)
})
