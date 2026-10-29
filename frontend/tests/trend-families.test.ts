// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TrendChart } from '../src/charts/TrendChart'
import { contrastRatio, deltaE, trendSeriesColors } from '../src/charts/trendColors'
import { getModelIcon } from '../src/utils'
import { getModelBadgeBackgroundColor } from '../src/model-badge'
import type { DailyUsage } from '../src/types'

vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ timezone: 'UTC', theme: 'light' }) }))

const period = '2026-10-26'
const data = [{ period, total_tokens: 900, total_cost_usd: 0.9, requests: 9 }] as DailyUsage[]
const models = ['claude-one', 'claude-two', 'gpt-one', 'gemini-one', 'deepseek-one', 'qwen-one', 'glm-one', 'grok-one', 'mystery']

/** The brand each model stands for, and the model that carries it. */
const BRANDS: Record<string, string> = {
  deepseek: 'deepseek-chat',
  openai: 'gpt-5',
  minimax: 'minimax-m2',
  claude: 'claude-sonnet-4',
  gemini: 'gemini-2.5-pro',
}

/** Four brands of one hue that have to be told apart by their lightness. */
const CLASHING = ['deepseek-chat', 'gemini-2.5-pro', 'glm-4.6', 'meta/llama-4']

/** A set of brands of its own hue, the kind a chart shows together. */
const TOGETHER = ['deepseek-chat', 'claude-sonnet-4', 'gemini-2.5-pro', 'minimax-m2', 'mimo-vl', 'qwen3-max']

/** Brands whose badge has no hue of its own. */
const GREY = ['gpt-5', 'grok-4', 'mystery']

/** Brands whose badge carries a hue. */
const COLOURED = ['deepseek-chat', 'claude-sonnet-4', 'gemini-2.5-pro', 'minimax-m2', 'mimo-vl', 'qwen3-max', 'poolside']

function hslOf(hex: string): { h: number; s: number; l: number } {
  const n = parseInt(hex.slice(1), 16)
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(c => c / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const d = max - min
  const l = (max + min) / 2
  if (!d) return { h: 0, s: 0, l }
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  const raw = max === r ? 60 * (((g - b) / d) % 6) : max === g ? 60 * ((b - r) / d + 2) : 60 * ((r - g) / d + 4)
  return { h: (raw + 360) % 360, s, l }
}

function hueDistance(a: number, b: number): number {
  const diff = Math.abs(a - b) % 360
  return diff > 180 ? 360 - diff : diff
}

/** The colour of one model, as the chart would draw it. */
function colorOf(model: string, theme: 'light' | 'dark'): string {
  return trendSeriesColors([{ key: model, model }], theme).get(model)!
}

async function chart(names: string[]) {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => names.map((dimension, i) => ({ dimension, period, total_tokens: (i + 1) * 100, total_cost_usd: 0.1, requests: i + 1 })) })))
  const result = render(createElement(TrendChart, { data, title: 'Trend' }))
  await waitFor(() => expect(result.container.querySelectorAll('path')).toHaveLength(names.length > 8 ? 8 : names.length))
  return result
}

afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('trend model families', () => {
  it('toggles the requests line and tooltip row', async () => {
    const { container } = await chart(models.slice(0, 1))
    const chartArea = container.querySelector('svg')!.parentElement!
    vi.spyOn(chartArea, 'getBoundingClientRect').mockReturnValue({ left: 0, right: 1000, width: 1000 } as DOMRect)
    fireEvent.pointerMove(container.querySelector('rect[fill="transparent"]')!, { clientX: 500 })
    const button = within(container).getByRole('button', { name: 'Hide requests' })
    expect(button.getAttribute('aria-pressed')).toBe('true')
    expect(container.querySelector('polyline')).toBeTruthy()
    expect(container.querySelectorAll('circle[stroke="var(--color-pink)"]').length).toBeGreaterThan(0)
    expect(within(container).getByText('Requests:')).toBeTruthy()

    fireEvent.click(button)
    expect(button.getAttribute('aria-pressed')).toBe('false')
    expect(container.querySelector('polyline')).toBeNull()
    expect(container.querySelectorAll('circle[stroke="var(--color-pink)"]')).toHaveLength(0)
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
    // The unknown badge is slate, so its chart colour stays grey.
    const grey = hslOf(colorOf('mystery', 'light'))
    expect(grey.s).toBeLessThanOrEqual(0.15 + 1 / 255)
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
    // The family is drawn in the colour of the badge of the model in it.
    const fills = [...container.querySelectorAll('path')].map(path => path.getAttribute('fill') ?? '')
    const badge = hslOf(getModelBadgeBackgroundColor('claude-one', 'light').slice(0, 7)).h
    expect(fills.some(fill => hueDistance(hslOf(fill).h, badge) <= 5)).toBe(true)
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
  it('gives every brand the hue of its badge', () => {
    for (const theme of ['light', 'dark'] as const) {
      for (const model of COLOURED) {
        const colour = colorOf(model, theme)
        const badge = hslOf(getModelBadgeBackgroundColor(model, theme).slice(0, 7)).h
        expect(hueDistance(hslOf(colour).h, badge), `${model} in ${theme}`).toBeLessThanOrEqual(5)
        expect(hslOf(colour).s, `${model} in ${theme}`).toBeCloseTo(0.95, 2)
      }
    }
  })

  it('keeps a brand without a hue of its own grey', () => {
    for (const theme of ['light', 'dark'] as const) {
      for (const model of GREY) {
        const colour = colorOf(model, theme)
        expect(hslOf(colour).s, `${model} in ${theme}`).toBeLessThanOrEqual(0.15 + 1 / 255)
      }
      // The unknown model keeps the hue of its slate badge.
      const badge = hslOf(getModelBadgeBackgroundColor('mystery', theme).slice(0, 7))
      expect(hueDistance(hslOf(colorOf('mystery', theme)).h, badge.h)).toBeLessThanOrEqual(5)
      expect(hslOf(colorOf('mystery', theme)).l).toBeCloseTo(theme === 'light' ? 0.50 : 0.60, 2)
    }
  })

  it('aims for the theme lightness when contrast allows it', () => {
    expect(hslOf(colorOf('deepseek-chat', 'light')).l).toBeCloseTo(0.50, 2)
    expect(hslOf(colorOf('deepseek-chat', 'dark')).l).toBeCloseTo(0.60, 2)
  })

  it('keeps every shown colour readable on the card of its theme', () => {
    for (const theme of ['light', 'dark'] as const) {
      const background = theme === 'light' ? '#ffffff' : '#1e293b'
      const colours = trendSeriesColors(TOGETHER.map(model => ({ key: model, model })), theme)
      for (const colour of colours.values()) {
        expect(contrastRatio(colour, background), `${colour} on ${background}`).toBeGreaterThanOrEqual(2)
      }
    }
  })

  it('turns a pale yellow badge into gold, not olive', () => {
    const colour = colorOf('mimo-vl', 'light')
    const { h, s } = hslOf(colour)
    expect(h).toBeGreaterThanOrEqual(35)
    expect(h).toBeLessThanOrEqual(55)
    expect(s).toBeCloseTo(0.95, 2)
  })

  it('tells the series shown together apart', () => {
    for (const theme of ['light', 'dark'] as const) {
      for (const set of [CLASHING, TOGETHER]) {
        const colours = [...trendSeriesColors(set.map(model => ({ key: model, model })), theme).values()]
        for (const [i, a] of colours.entries()) {
          for (const [j, b] of colours.entries()) {
            if (i >= j) continue
            expect(deltaE(a, b), `${a} vs ${b} in ${theme}`).toBeGreaterThanOrEqual(20)
          }
        }
      }
    }
  })

  it('gives a family the colour of the model in it', () => {
    for (const theme of ['light', 'dark'] as const) {
      for (const [brand, model] of Object.entries(BRANDS)) {
        const modelColour = colorOf(model, theme)
        const familyColour = trendSeriesColors([{ key: brand, model }], theme).get(brand)!
        expect(familyColour, `${brand} in ${theme}`).toBe(modelColour)
      }
    }
  })

  it('names no brand in the colour rules', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/charts/trendColors.ts'), 'utf8')
    for (const brand of ['claude', 'gpt', 'gemini', 'minimax', 'mimo', 'deepseek', 'grok', 'qwen', 'glm', 'opus', 'sonnet', 'xiaomi']) {
      expect(source.toLowerCase(), brand).not.toContain(brand)
    }
  })
})
