// @vitest-environment jsdom
import { createElement } from 'react'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../src/App'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

it('shows whole requests and tokens while the KPIs count up', async () => {
  const row = { provider: 'p', model: 'm', requests: 7, prompt_tokens: 400, completion_tokens: 123, cached_tokens: 0, cache_creation_tokens: 0, total_tokens: 523, total_cost_usd: 1.5, latency_sum_ms: 1000 }
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  let summaryServed = false
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = new URL(String(input), window.location.origin).pathname
    const data = path === '/auth/me' ? { provider: 'google', user: { id: 'u', email: 'a@example.com' } }
      : path === '/usage/summary' ? [row] : path === '/usage/count' ? { total: 7 } : []
    return { ok: true, status: 200, json: async () => { if (path === '/usage/summary') summaryServed = true; return data } } as Response
  }))
  // Hold the count-up mid-way: frames run only when the test steps them. A
  // cancelled frame is dropped, so a target that changes late restarts cleanly.
  const frames = new Map<number, FrameRequestCallback>()
  let nextFrame = 0
  // Count-up frames queued once the summary was served, i.e. towards its totals.
  let loadedCountUps = 0
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    if (summaryServed && cb.name === 'animate') loadedCountUps++
    frames.set(++nextFrame, cb)
    return nextFrame
  })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { frames.delete(id) })
  const step = (ts: number) => act(async () => {
    const due = [...frames.values()]
    frames.clear()
    due.forEach(cb => cb(ts))
  })
  window.history.replaceState({}, '', '/dashboard')
  render(createElement(App))
  await screen.findByText('Requests', { selector: '.stat-label' })
  // useCountUp queues its first frame in an effect after the data arrives.
  await vi.waitFor(() => expect(loadedCountUps).toBeGreaterThan(0))
  await step(0)
  await step(100)
  const value = (label: string) => screen.getByText(label, { selector: '.stat-label' }).nextElementSibling!.textContent!
  expect(value('Requests')).toMatch(/^\d+$/)
  expect(Number(value('Requests'))).toBeGreaterThan(0)
  expect(Number(value('Requests'))).toBeLessThan(7)
  expect(value('Token Usage')).toMatch(/^\d+$/)
  // Cost keeps its cents mid-way.
  expect(value('Estimated Cost')).toMatch(/^\$\d+\.\d{2}$/)
})
