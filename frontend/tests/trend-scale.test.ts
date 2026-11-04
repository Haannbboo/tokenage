// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrendChart } from '../src/charts/TrendChart'
import { trendDotScale, trendLabelIndices } from '../src/charts/trendScale'
import type { DailyUsage } from '../src/types'

vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ timezone: 'UTC', theme: 'light' }) }))

const row = (period: string): DailyUsage => ({
  period, requests: 3, prompt_tokens: 60, completion_tokens: 40, cached_tokens: 10,
  cache_creation_tokens: 0, total_tokens: 100, input_cost_usd: 0.1, output_cost_usd: 0.4,
  total_cost_usd: 0.5, avg_latency_ms: 10, latency_sum_ms: 30, avg_throughput: 1,
  successful_requests: 3, failed_requests: 0, status_429: 0, status_4xx: 0,
  status_5xx: 0, status_unknown: 0,
})

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('usage trend request dots', () => {
  it('counter-scales the x axis so a dot keeps the same round size on every panel', () => {
    // The panel squeezes the x axis, so a narrow one needs a bigger counter-scale.
    expect(trendDotScale(380)).toBeGreaterThan(trendDotScale(1200))
    // The screen radius of a dot must not depend on the panel width.
    const radiusAt = (scale: number, width: number) => 3 * scale * (width / 1000)
    expect(radiusAt(trendDotScale(380), 380)).toBeCloseTo(radiusAt(trendDotScale(1200), 1200), 5)
  })

  it('draws the dots inside the counter-scaled group instead of a stretched circle', async () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })))
    const { container } = render(createElement(TrendChart, {
      data: [row('2026-10-26'), row('2026-10-27')],
      title: 'Trend',
    }))
    // The plot waits for the model split.
    await waitFor(() => expect(container.querySelector('circle')).toBeTruthy())
    const scaled = [...container.querySelectorAll('g')].filter(node => (node.getAttribute('style') ?? '').includes('scale('))
    expect(scaled.length).toBeGreaterThan(0)
    expect(scaled[0].querySelector('circle')).toBeTruthy()
  })
})


it('shows a model fetch failure and clears it after a successful retry', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  const fetchMock = vi.fn().mockResolvedValueOnce({ ok: false }).mockResolvedValue({ ok: true, json: async () => [] })
  vi.stubGlobal('fetch', fetchMock)
  const { rerender } = render(createElement(TrendChart, { data: [row('2026-10-26')], title: 'Trend', filterParams: { provider: 'first' } }))
  await screen.findByRole('alert')
  rerender(createElement(TrendChart, { data: [row('2026-10-26')], title: 'Trend', filterParams: { provider: 'second' } }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
})

it('refetches the model split on refresh even when the filter values are unchanged', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  const fetchMock = vi.fn(async () => new Response('[]'))
  vi.stubGlobal('fetch', fetchMock)
  const props = { data: [row('2026-10-26')], title: 'Trend' }
  const { rerender } = render(createElement(TrendChart, { ...props, filterParams: { since: null } }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
  rerender(createElement(TrendChart, { ...props, filterParams: { since: null } }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
})

describe('trend x-axis labels', () => {
  it('thins labels to fit the width, keeping the first and last', () => {
    const phone = trendLabelIndices(24, 300)
    expect(phone[0]).toBe(0)
    expect(phone[phone.length - 1]).toBe(23)
    expect(phone.length).toBeLessThanOrEqual(Math.floor(300 / 44))
    const wide = trendLabelIndices(24, 1200)
    expect(wide.length).toBeLessThanOrEqual(12)
    expect(wide.length).toBeGreaterThan(phone.length)
    expect(trendLabelIndices(7, 1200)).toEqual([0, 1, 2, 3, 4, 5, 6])
    expect(trendLabelIndices(1, 300)).toEqual([0])
    expect(trendLabelIndices(0, 300)).toEqual([])
    for (let count = 1; count <= 60; count++) {
      for (const width of [100, 300, 500, 1200]) {
        const shown = trendLabelIndices(count, width)
        expect(shown.length).toBeLessThanOrEqual(Math.min(12, Math.max(2, Math.floor(width / 44)), count))
        expect(shown[shown.length - 1]).toBe(count - 1)
      }
    }
    // No two shown labels closer than a step, which is at least one label wide.
    for (const [shown, width] of [[phone, 300], [wide, 1200]] as const) {
      const gaps = shown.slice(1).map((n, i) => n - shown[i])
      expect(Math.min(...gaps)).toBeGreaterThanOrEqual(Math.ceil(24 / Math.min(12, Math.floor(width / 44))))
    }
  })
})

it('keeps a model stack taller than the daily total inside the plot', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  // The daily total says 100 tokens, the model split 500 + 400.
  const split = ['a', 'b'].map((dimension, i) => ({ period: '2026-10-26', dimension, total_tokens: i ? 400 : 500, total_cost_usd: 1, requests: 1 }))
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => split })))
  const { container } = render(createElement(TrendChart, { data: [row('2026-10-26')], title: 'Trend' }))
  await waitFor(() => expect(container.querySelectorAll('.trend-segment')).toHaveLength(2))
  const ys = [...container.querySelectorAll('.trend-segment')]
    .flatMap(path => [...(path.getAttribute('d') ?? '').matchAll(/(-?[\d.]+),(-?[\d.]+)/g)].map(m => Number(m[2])))
  // The stack fills the 200-unit plot exactly: the bars scale to the taller of the two.
  expect(Math.min(...ys)).toBeCloseTo(0, 5)
  expect(Math.max(...ys)).toBeCloseTo(200, 5)
})

it('offers a scoped Retry when the model split fails, keeping the totals, and Retry reloads it', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  const fetchMock = vi.fn().mockResolvedValueOnce({ ok: false }).mockResolvedValue({ ok: true, json: async () => [] })
  vi.stubGlobal('fetch', fetchMock)
  const { container } = render(createElement(TrendChart, { data: [row('2026-10-26')], title: 'Trend' }))
  const alert = await screen.findByRole('alert')
  // Only the split failed: the chart keeps the totals' requests line.
  expect(container.querySelector('polyline.trend-requests')).toBeTruthy()
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull())
  expect(fetchMock).toHaveBeenCalledTimes(2)
})
