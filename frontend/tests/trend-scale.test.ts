// @vitest-environment jsdom
import { createElement } from 'react'
import { render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrendChart } from '../src/charts/TrendChart'
import { trendDotScale } from '../src/charts/trendScale'
import type { DailyUsage } from '../src/types'

vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ timezone: 'UTC', theme: 'light' }) }))

const row = (period: string): DailyUsage => ({
  period, requests: 3, prompt_tokens: 60, completion_tokens: 40, cached_tokens: 10,
  cache_creation_tokens: 0, total_tokens: 100, input_cost_usd: 0.1, output_cost_usd: 0.4,
  total_cost_usd: 0.5, avg_latency_ms: 10, latency_sum_ms: 30, avg_throughput: 1,
  successful_requests: 3, failed_requests: 0, status_429: 0, status_4xx: 0,
  status_5xx: 0, status_unknown: 0,
})

afterEach(() => { vi.unstubAllGlobals() })

describe('usage trend request dots', () => {
  it('counter-scales the x axis so a dot keeps the same round size on every panel', () => {
    // The panel squeezes the x axis, so a narrow one needs a bigger counter-scale.
    expect(trendDotScale(380)).toBeGreaterThan(trendDotScale(1200))
    // The screen radius of a dot must not depend on the panel width.
    const radiusAt = (scale: number, width: number) => 3 * scale * (width / 1000)
    expect(radiusAt(trendDotScale(380), 380)).toBeCloseTo(radiusAt(trendDotScale(1200), 1200), 5)
  })

  it('draws the dots inside the counter-scaled group instead of a stretched circle', () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => [] })))
    const { container } = render(createElement(TrendChart, {
      data: [row('2026-10-26'), row('2026-10-27')],
      title: 'Trend',
    }))
    const scaled = [...container.querySelectorAll('g')].filter(node => (node.getAttribute('style') ?? '').includes('scale('))
    expect(scaled.length).toBeGreaterThan(0)
    expect(scaled[0].querySelector('circle')).toBeTruthy()
  })
})
