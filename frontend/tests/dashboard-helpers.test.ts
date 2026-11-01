import { describe, expect, it, vi } from 'vitest'
import { fillPeriodGaps } from '../src/periodGaps'
import { describePeriod, periodLabelText } from '../src/periodLabels'
import { sparklineScale } from '../src/sparklineScale'
import { sortTrendRows, trendTooltipX } from '../src/charts/trendTooltip'
import { trendBrandColor, vibrantColor } from '../src/charts/trendColors'
import type { DailyUsage } from '../src/types'

// The i18n module reads the saved language from localStorage on import.
vi.mock('../src/i18n/index.ts', () => ({ t: (value: string) => value }))

const row = (period: string): DailyUsage => ({
  period, requests: 1, prompt_tokens: 5, completion_tokens: 5, cached_tokens: 0,
  cache_creation_tokens: 0, total_tokens: 10, input_cost_usd: 0.1, output_cost_usd: 0.4,
  total_cost_usd: 0.5, avg_latency_ms: 10, latency_sum_ms: 10, avg_throughput: 1,
  successful_requests: 1, failed_requests: 0, status_429: 0, status_4xx: 0,
  status_5xx: 0, status_unknown: 0,
})

describe('sparkline scale', () => {
  it('keeps the zero-based scale for magnitudes', () => {
    expect(sparklineScale([0, 50, 100], 'zero')).toMatchObject({ lo: 0, hi: 100, span: 100 })
  })

  it('zooms into a rate that stays high instead of drawing it flat', () => {
    const { span } = sparklineScale([96.8, 97.1, 96.9, 97.2], 'relative')
    expect(span).toBeLessThan(5)
    expect(span).toBeGreaterThan(0.4)
  })

  it('grows the zoomed window smoothly with the spread', () => {
    const narrow = sparklineScale([96.0, 97.9], 'relative')
    const wide = sparklineScale([95.9, 97.9], 'relative')
    expect(Math.abs(narrow.span - wide.span)).toBeLessThan(0.5)
  })

  it('draws a constant series as a flat line in the middle of the box', () => {
    const { lo, hi } = sparklineScale([50, 50, 50], 'relative')
    expect(lo).toBeLessThan(50)
    expect(hi).toBeGreaterThan(50)
  })

  it('reads an empty bucket as zero without blowing up the zoomed window', () => {
    expect(sparklineScale([0, 96.8, 97.1, 0], 'relative').span).toBe(sparklineScale([96.8, 97.1], 'relative').span)
  })

  it('keeps an all-zero series on a usable scale', () => {
    const { span } = sparklineScale([0, 0])
    expect(Number.isFinite(span)).toBe(true)
    expect(span).toBeGreaterThan(0)
  })
})

describe('sparkline gap filling', () => {
  it('inserts a day without usage as a zero bucket', () => {
    const filled = fillPeriodGaps([row('2026-10-20'), row('2026-10-22')], 'day')
    expect(filled.map(r => r.period)).toEqual(['2026-10-20', '2026-10-21', '2026-10-22'])
    expect(filled[1].requests).toBe(0)
    expect(filled[1].total_tokens).toBe(0)
  })

  it('keeps the rows that had usage as they are', () => {
    const filled = fillPeriodGaps([row('2026-10-20'), row('2026-10-22')], 'day')
    expect(filled[0].requests).toBe(1)
    expect(filled[2].requests).toBe(1)
    expect(filled[2].total_cost_usd).toBe(0.5)
  })

  it('inserts an hour without usage as a zero bucket', () => {
    const filled = fillPeriodGaps([row('2026-10-20 12:00'), row('2026-10-20 14:00')], 'hour')
    expect(filled.map(r => r.period)).toEqual(['2026-10-20 12:00', '2026-10-20 13:00', '2026-10-20 14:00'])
    expect(filled[1].requests).toBe(0)
  })

  it('keeps the series inside the range the data covers', () => {
    const filled = fillPeriodGaps([row('2026-10-20'), row('2026-10-22')], 'day')
    expect(filled[0].period).toBe('2026-10-20')
    expect(filled[filled.length - 1].period).toBe('2026-10-22')
  })

  it('leaves a single row alone', () => {
    expect(fillPeriodGaps([row('2026-10-20')], 'day')).toHaveLength(1)
  })
})

describe('period labels', () => {
  const now = Date.UTC(2026, 9, 22, 15, 0) // 2026-10-22 15:00

  it('marks a bucket of today or yesterday as relative', () => {
    expect(describePeriod('2026-10-22', 'day', now, 'en-US')?.relative).toBe('today')
    expect(describePeriod('2026-10-21', 'day', now, 'en-US')?.relative).toBe('yesterday')
  })

  it('names an older day with no relative word', () => {
    const label = describePeriod('2026-10-20', 'day', now, 'en-US')
    expect(label?.relative).toBeUndefined()
    expect(label?.day).toMatch(/Oct 20/)
  })

  it('gives an hour bucket the clock and a day bucket none', () => {
    const hour = describePeriod('2026-10-22 13:00', 'hour', now, 'en-US')
    expect(hour?.time).toMatch(/1:00|13:00/)
    expect(describePeriod('2026-10-20', 'day', now, 'en-US')?.time).toBeUndefined()
  })

  it('has no label for an unknown period', () => {
    expect(describePeriod('not-a-period', 'day', now, 'en-US')).toBeNull()
  })

  it('reads as a sentence', () => {
    const today = describePeriod('2026-10-22 13:00', 'hour', now, 'en-US')
    expect(periodLabelText(today)).toMatch(/^Today /)
    expect(periodLabelText(describePeriod('2026-10-20', 'day', now, 'en-US'))).toMatch(/Oct 20/)
  })
})

describe('trend tooltip helpers', () => {
  const rows = [
    { model: 'a', tokens: 10, cost: 1 },
    { model: 'b', tokens: 30, cost: 0.5 },
    { model: 'c', tokens: 20, cost: 2 },
  ]

  it('orders the model rows by the active metric', () => {
    expect(sortTrendRows(rows, 'tokens').map(r => r.model)).toEqual(['b', 'c', 'a'])
    expect(sortTrendRows(rows, 'cost').map(r => r.model)).toEqual(['c', 'a', 'b'])
  })

  it('breaks ties with the other metric', () => {
    const tied = [{ model: 'a', tokens: 10, cost: 1 }, { model: 'b', tokens: 20, cost: 1 }]
    expect(sortTrendRows(tied, 'cost').map(r => r.model)).toEqual(['b', 'a'])
    expect(sortTrendRows(tied, 'tokens').map(r => r.model)).toEqual(['b', 'a'])
  })

  it('centres on the cursor in the middle', () => {
    expect(trendTooltipX(500, 1000, 520, 192)).toBe(500)
  })

  it('keeps moving near the edges instead of freezing', () => {
    expect(trendTooltipX(0, 1000, 520, 192)).toBeLessThan(trendTooltipX(60, 1000, 520, 192))
    expect(trendTooltipX(1000, 1000, 520, 192)).toBeGreaterThan(trendTooltipX(940, 1000, 520, 192))
  })

  it('never drifts past the available overflow', () => {
    expect(trendTooltipX(0, 1000, 520, 0)).toBe(268)
    expect(trendTooltipX(0, 1000, 520, 192)).toBe(76)
    expect(trendTooltipX(1000, 1000, 520, 0)).toBe(732)
    expect(trendTooltipX(1000, 1000, 520, 192)).toBe(924)
  })

  it('handles a zero-width chart', () => {
    expect(trendTooltipX(10, 0, 520, 0)).toBe(0)
  })
})

describe('trend colours', () => {
  it('pins the Claude variants and MiniMax to distinct brand shades', () => {
    expect(trendBrandColor('claude-opus-5-5', '#cc7c5e')).toBe('#9a5b3c')
    expect(trendBrandColor('claude-sonnet-5-5', '#cc7c5e')).toBe('#c98a63')
    expect(trendBrandColor('MiniMax-M2', '#ec6b53')).toBe('#ef4444')
    expect(trendBrandColor('claude-haiku', '#cc7c5e')).toBe('#cc7c5e')
  })

  it('boosts the chroma of the brand colours', () => {
    const chroma = (hex: string) => {
      const n = parseInt(hex.slice(1), 16)
      const r = (n >> 16) & 255
      const g = (n >> 8) & 255
      const b = n & 255
      return Math.max(r, g, b) - Math.min(r, g, b)
    }
    for (const base of ['#cc7c5e', '#475569', '#dcc496', '#4d7cff']) {
      expect(chroma(vibrantColor(base))).toBeGreaterThan(chroma(base))
    }
  })
})
