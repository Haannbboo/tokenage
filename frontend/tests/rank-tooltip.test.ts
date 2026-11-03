// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HorizontalBarChart, RankTooltipBody } from '../src/charts/HorizontalBarChart'
import type { BarItem, Metric } from '../src/charts/HorizontalBarChart'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const item: BarItem = {
  name: 'claude-opus-5-5', icon: null, color: 'red',
  tokens: 1_000_000, promptTokens: 900_000, cachedTokens: 600_000, completionTokens: 100_000, cacheWriteTokens: 50_000,
  cost: 12.5, pricePerMillion: 3, throughput: 42, successRate: 75, cacheHitRate: 63.2,
  successful: 3, failed: 1, totalTime: 2_000, avgTime: 500,
}

// Label/value pairs as rendered, e.g. "Cached 600K".
function rows(metric: Metric, share: string | null = null) {
  render(createElement(RankTooltipBody, { item, metric, rank: 2, share }))
  return [...document.querySelectorAll('.chart-tip-row')].map((row) =>
    `${row.querySelector('.chart-tip-label')!.textContent} ${row.querySelector('.chart-tip-value')!.textContent}`)
}

describe('rank tooltip content', () => {
  it('breaks tokens into cached, input and output with a total', () => {
    expect(rows('tokens')).toEqual(['Cached 600K', 'Input 300K', 'Output 100K', 'Total 1M'])
    expect(document.querySelector('.chart-tip-title')!.textContent).toBe('claude-opus-5-5#2')
  })
  it('shows tokens, price and share for cost', () => {
    expect(rows('cost', '40.0%')).toEqual(['Tokens 1M', 'Price $3.000/1M', 'Share 40.0%', 'Cost $12.50'])
  })
  it('shows cached vs input and cache write for the hit rate', () => {
    expect(rows('cacheHitRate')).toEqual(['Cached 600K', 'Input 300K', 'Cache write 50K', 'Hit rate 63.2%'])
  })
  it('shows success and failure counts', () => {
    expect(rows('successRate')).toEqual(['Successful 3', 'Failed 1', 'Success rate 75.0%'])
  })
  it('shows the other tool metrics and the share', () => {
    expect(rows('count', '10.0%')).toEqual(['Total time 2.00s', 'Avg time 500ms', 'Share 10.0%', 'Calls 1,000,000'])
  })
})

describe('rank row interaction', () => {
  function renderChart() {
    render(createElement(HorizontalBarChart, { title: 'Top', items: [item, { ...item, name: 'gpt', tokens: 10 }], metric: 'tokens' }))
    return screen.getAllByRole('listitem')
  }

  it('shows the tooltip on keyboard focus and hides it on Escape', () => {
    const [first] = renderChart()
    expect(first.tabIndex).toBe(0)
    fireEvent.focus(first)
    const tip = screen.getByRole('tooltip')
    expect(tip.textContent).toContain('claude-opus-5-5')
    expect(first.getAttribute('aria-describedby')).toBe(tip.id)
    expect(first.classList.contains('is-active')).toBe(true)
    fireEvent.keyDown(first, { key: 'Escape' })
    expect(screen.queryByRole('tooltip')).toBeNull()
    expect(first.classList.contains('is-active')).toBe(false)
  })

  it('restores the focused row when the pointer leaves', () => {
    const [first, second] = renderChart()
    first.focus()
    fireEvent.mouseEnter(second)
    fireEvent.mouseLeave(second.parentElement!)
    expect(first.classList.contains('is-active')).toBe(true)
    expect(screen.getByRole('tooltip').textContent).toContain('claude-opus-5-5')
  })

  it('tracks duplicate names as separate rows on hover and focus', () => {
    render(createElement(HorizontalBarChart, { title: 'Top', items: [
      { ...item, name: 'MiniMax', tokens: 10 },
      { ...item, name: 'MiniMax', tokens: 20 },
    ], metric: 'tokens' }))
    const [first, second] = screen.getAllByRole('listitem')
    fireEvent.mouseEnter(second)
    expect(second.classList.contains('is-active')).toBe(true)
    expect(first.classList.contains('is-active')).toBe(false)
    expect(screen.getByRole('tooltip').textContent).toContain('#2')
    expect(screen.getByRole('tooltip').textContent).toContain('Total10')
    fireEvent.focus(first)
    expect(first.classList.contains('is-active')).toBe(true)
    expect(second.classList.contains('is-active')).toBe(false)
    expect(screen.getByRole('tooltip').textContent).toContain('#1')
    expect(screen.getByRole('tooltip').textContent).toContain('Total20')
  })

  it('hides the tooltip on blur and follows hover between rows', () => {
    const [first, second] = renderChart()
    const hasFocus = vi.spyOn(document, 'hasFocus').mockReturnValue(false)
    fireEvent.focus(first)
    fireEvent.blur(first)
    expect(screen.getByRole('tooltip')).toBeTruthy() // window blur keeps it
    hasFocus.mockReturnValue(true)
    fireEvent.blur(first)
    expect(screen.queryByRole('tooltip')).toBeNull()
    fireEvent.mouseEnter(second)
    expect(screen.getByRole('tooltip').textContent).toContain('gpt')
    fireEvent.mouseLeave(second.parentElement!)
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
