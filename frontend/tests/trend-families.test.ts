// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrendChart } from '../src/charts/TrendChart'
import { trendSeriesColors } from '../src/charts/trendColors'
import { getModelIcon } from '../src/utils'
import type { DailyUsage } from '../src/types'

vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ timezone: 'UTC', theme: 'light' }) }))

const period = '2026-10-26'
const data = [{ period, total_tokens: 900, total_cost_usd: 0.9, requests: 9 }] as DailyUsage[]
const models = ['claude-one', 'claude-two', 'gpt-one', 'gemini-one', 'deepseek-one', 'qwen-one', 'glm-one', 'grok-one', 'mystery']

async function chart(names: string[], zeroCost?: string) {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => names.map((dimension, i) => ({ dimension, period, total_tokens: (i + 1) * 100, total_cost_usd: dimension === zeroCost ? 0 : 0.1, requests: i + 1 })) })))
  const result = render(createElement(TrendChart, { data, title: 'Trend' }))
  await waitFor(() => expect(result.container.querySelectorAll('path')).toHaveLength(names.length > 8 ? 8 : names.length))
  return result
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('trend model families', () => {
  it.each([
    ['model', models.slice(0, 3), 'claude-one'],
    ['family', models, 'Claude'],
  ])('highlights and restores a %s legend series', async (_mode, names, label) => {
    const { container } = await chart(names)
    const legend = within(container).getByText(label).parentElement!
    const color = (legend.firstElementChild as HTMLElement).style.background
    const paths = [...container.querySelectorAll('path')]
    const matchesColor = (path: SVGPathElement) => {
      const swatch = document.createElement('div')
      swatch.style.background = path.getAttribute('fill') ?? ''
      return swatch.style.background === color
    }
    const selected = paths.filter(matchesColor)
    const others = paths.filter(path => !matchesColor(path))
    expect(selected.length).toBeGreaterThan(0)
    expect(others.length).toBeGreaterThan(0)
    const shapes = paths.map(path => path.getAttribute('d'))
    fireEvent.mouseEnter(legend)
    expect(selected.every(path => path.classList.contains('is-highlighted'))).toBe(true)
    expect(others.every(path => path.classList.contains('is-dimmed'))).toBe(true)
    expect(paths.map(path => path.getAttribute('d'))).toEqual(shapes)
    expect(container.querySelector('polyline')).toBeTruthy()
    fireEvent.mouseLeave(legend.parentElement!)
    fireEvent.click(within(container).getByRole('button', { name: 'Cost' }))
    fireEvent.mouseEnter(legend)
    expect([...container.querySelectorAll('path.is-dimmed')].length).toBeGreaterThan(0)
    fireEvent.mouseLeave(legend.parentElement!)
    expect(container.querySelectorAll('path.is-dimmed, path.is-highlighted')).toHaveLength(0)
  })

  it('does not dim cost bars for a series with no painted cost segment', async () => {
    const { container } = await chart(models.slice(0, 3), 'claude-one')
    fireEvent.click(within(container).getByRole('button', { name: 'Cost' }))
    fireEvent.mouseEnter(within(container).getByText('claude-one').parentElement!)
    expect(container.querySelectorAll('path')).toHaveLength(2)
    expect(container.querySelectorAll('path.is-dimmed, path.is-highlighted')).toHaveLength(0)
    expect(container.querySelectorAll('.trend-legend-entry.is-dimmed, .trend-legend-entry.is-highlighted')).toHaveLength(0)
    fireEvent.mouseLeave(container.querySelector('.trend-legend')!)
    fireEvent.mouseEnter(within(container).getByText('gpt-one').parentElement!)
    expect(container.querySelectorAll('path.is-highlighted')).toHaveLength(1)
    expect(container.querySelectorAll('path.is-dimmed')).toHaveLength(1)
  })

  it('toggles the requests line and tooltip row', async () => {
    const { container } = await chart(models.slice(0, 1))
    const chartArea = container.querySelector('svg')!.parentElement!
    vi.spyOn(chartArea, 'getBoundingClientRect').mockReturnValue({ left: 0, right: 1000, width: 1000 } as DOMRect)
    fireEvent.pointerMove(container.querySelector('rect[fill="transparent"]')!, { clientX: 500 })
    const button = within(container).getByRole('button', { name: 'Hide requests' })
    expect(button.getAttribute('aria-pressed')).toBe('true')
    expect(container.querySelector('polyline')).toBeTruthy()
    expect(container.querySelectorAll('circle[stroke="var(--text-secondary)"]').length).toBeGreaterThan(0)
    expect(within(container).getByText('Requests:')).toBeTruthy()

    fireEvent.click(button)
    expect(button.getAttribute('aria-pressed')).toBe('false')
    expect(container.querySelector('polyline')).toBeNull()
    expect(container.querySelectorAll('circle[stroke="var(--text-secondary)"]')).toHaveLength(0)
    expect(within(container).queryByText('Requests:')).toBeNull()

    fireEvent.click(button)
    expect(button.getAttribute('aria-pressed')).toBe('true')
    expect(container.querySelector('polyline')).toBeTruthy()
    expect(within(container).getByText('Requests:')).toBeTruthy()
  })

  it('shows each model through eight and does not pool unknown models', async () => {
    const { container } = await chart(models.slice(1))
    expect(within(container).getByText('mystery')).toBeTruthy()
    expect(within(container).queryByText('Other')).toBeNull()
  })

  it('groups nine models by family and lists each model in the bucket tooltip', async () => {
    const { container } = await chart(models)
    expect(within(container).getByText('Claude')).toBeTruthy()
    expect(within(container).getByText('Other')).toBeTruthy()
    expect(within(container).queryByText('claude-one')).toBeNull()
    const chartArea = container.querySelector('svg')!.parentElement!
    vi.spyOn(chartArea, 'getBoundingClientRect').mockReturnValue({ left: 0, right: 1000, width: 1000 } as DOMRect)
    fireEvent.pointerMove(container.querySelector('rect[fill="transparent"]')!, { clientX: 500 })
    expect(within(container).getByText('claude-one')).toBeTruthy()
    expect(within(container).getByText('claude-two')).toBeTruthy()
    expect(within(container).getByText('mystery')).toBeTruthy()
    expect(within(container).getByText('100')).toBeTruthy()
    expect(within(container).queryByText(/\d+ Requests/)).toBeNull()
    expect(within(container).getByText('Requests:')).toBeTruthy()
    expect(within(container.querySelector('.widget-header')!).getByText('Requests')).toBeTruthy()
    // Families are drawn in the shared palette, in legend order.
    const fills = [...container.querySelectorAll('path')].map(path => path.getAttribute('fill') ?? '')
    expect(fills.every(fill => fill.startsWith('var(--series-'))).toBe(true)
  })

  it('keeps existing icon matches and theme variants', () => {
    expect(getModelIcon('tencent/gpt', 'dark')?.props.src).toBe('/models/hunyuan-color.svg')
    expect(getModelIcon('gpt-4', 'dark')?.props.src).toBe('/models/openai-light.svg')
    expect(getModelIcon('claude-sonnet', 'light')?.props.src).toBe('/models/claude-ai-icon.svg')
    expect(getModelIcon('x-ai/grok', 'dark')?.props.src).toBe('/models/grok-dark.svg')
    expect(getModelIcon('unknown', 'light')).toBeNull()
  })
})

describe('trend colours', () => {
  it('gives the series the shared palette in legend order, green first', () => {
    const colours = [...trendSeriesColors(Array.from({ length: 9 }, (_, i) => ({ key: `s${i}` }))).values()]
    expect(colours.slice(0, 8)).toEqual(Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`))
    expect(colours[8]).toBe('var(--series-1)')
  })

  it('defines every series colour for light and dark', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/index.css'), 'utf8')
    const [light, dark] = css.split('[data-theme="dark"]')
    for (let i = 1; i <= 8; i++) expect(light, `--series-${i}`).toContain(`--series-${i}:`)
    // The dark block either redefines a series or the accent it points at.
    for (const name of ['--color-green', '--chart-2', '--color-blue', '--color-yellow', '--color-purple', '--color-pink', '--series-7', '--series-8']) {
      expect(dark, name).toContain(`${name}:`)
    }
  })
})
