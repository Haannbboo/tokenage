// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { EffortLevel, EffortMixSection } from '../src/effort'
import { renderRequestCell } from '../src/components/RequestCell'
import { RequestDetail } from '../src/components/RequestDetail'
import type { UsageRow } from '../src/types'

afterEach(cleanup)
const pips = (effort: string) => {
  const el = render(createElement(EffortLevel, { effort })).container
  const all = el.querySelectorAll('.effort-pips i')
  return { all: all.length, on: el.querySelectorAll('.effort-pips i.on').length, text: el.textContent, hidden: el.querySelector('.effort-pips')?.getAttribute('aria-hidden') }
}

describe('effort level pips', () => {
  it('fills pips by level and keeps the text as the label', () => {
    const filled = Object.fromEntries(['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'].map(level => {
      const p = pips(level)
      expect(p).toMatchObject({ all: 5, text: level, hidden: 'true' })
      cleanup()
      return [level, p.on]
    }))
    expect(filled).toEqual({ none: 0, minimal: 1, low: 1, medium: 2, high: 3, xhigh: 4, max: 5, ultra: 5 })
  })

  it('colours each level by its tone, sharing one for near-synonyms and muting default', () => {
    const tones = Object.fromEntries(['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra', 'default', 'turbo'].map(level => {
      const el = render(createElement(EffortLevel, { effort: level })).container
      const tone = el.querySelector('.effort-level')!.className.replace('effort-level ', '')
      expect(el.querySelector('.effort-name')!.textContent).toBe(level)
      cleanup()
      return [level, tone]
    }))
    expect(tones).toEqual({ none: 'effort-tone-none', minimal: 'effort-tone-none', low: 'effort-tone-low', medium: 'effort-tone-medium', high: 'effort-tone-high', xhigh: 'effort-tone-xhigh', max: 'effort-tone-max', ultra: 'effort-tone-max', default: 'effort-tone-default', turbo: 'effort-tone-default' })
  })

  it('backs every tone with a token defined for both themes', () => {
    const app = readFileSync('src/App.css', 'utf8')
    const index = readFileSync('src/index.css', 'utf8')
    const tokens = { none: '--effort-none', low: '--effort-low', medium: '--effort-medium', high: '--effort-high', xhigh: '--effort-xhigh', max: '--effort-max-gradient', default: '--text-muted' }
    for (const [tone, token] of Object.entries(tokens)) {
      expect(app).toContain(`.effort-tone-${tone} { --effort: var(${token}); }`)
      expect(index.split(`${token}:`).length - 1).toBe(2)
    }
    expect(app).toMatch(/\.effort-tone-max \.effort-name, \.effort-tone-max b \{[^}]*background-clip: text;[^}]*color: transparent;/)
    expect(index).not.toMatch(/--effort-\d/)
  })

  it('shows default as text only', () => {
    expect(pips('default')).toMatchObject({ all: 0, text: 'default' })
  })

  it('leaves the log cell blank and drops the drawer item when effort is null', () => {
    const row = { id: 'r1', effort: null, client_source: 'codex', endpoint: '/v1' } as unknown as UsageRow
    const opts = { colWidths: { model: 200, device: 120 }, hoveredSession: null, setSessionFilter: () => {}, recalculatingId: null, handleRecalculateCost: async () => {} }
    const table = (effort: string | null) => render(createElement('table', null, createElement('tbody', null, createElement('tr', null, renderRequestCell({ ...row, effort }, 'effort', opts))))).container
    expect(table(null).querySelector('td')!.innerHTML).toBe('')
    expect(table('high').querySelector('td')!.textContent).toBe('high')
    const detail = (effort: string | null) => render(createElement(RequestDetail, { row: { ...row, effort }, toolCalls: null, showToast: () => {} })).container.textContent
    expect(detail(null)).not.toContain('Effort')
    expect(detail('xhigh')).toContain('Effortxhigh')
  })
})

describe('effort mix', () => {
  it('orders levels low to high, then default, leaving out unreported requests', () => {
    const view = render(createElement(EffortMixSection, { mix: [{ effort: null, requests: 85 }, { effort: 'high', requests: 401 }, { effort: 'default', requests: 57 }, { effort: 'medium', requests: 512 }, { effort: 'low', requests: 61 }, { effort: 'xhigh', requests: 88 }] })).container
    expect(view.querySelector('[role="img"]')!.getAttribute('aria-label')).toBe('low 5%, medium 46%, high 36%, xhigh 8%, default 5%')
    expect(view.querySelector('.effort-mix-head span')!.textContent).toBe('1,119 of 1,204 requests')
  })

  it('renders a labelled bar and legend without a not-reported entry', () => {
    const view = render(createElement(EffortMixSection, { mix: [{ effort: null, requests: 1 }, { effort: 'high', requests: 3 }, { effort: 'low', requests: 1 }] })).container
    expect(view.querySelector('[role="img"]')!.getAttribute('aria-label')).toBe('low 25%, high 75%')
    expect([...view.querySelectorAll<HTMLElement>('.effort-mix-bar i')].map(i => i.style.flex)).toEqual(['1', '3'].map(n => expect.stringMatching(new RegExp(`^${n}`))))
    expect(view.querySelector('.effort-mix-legend')!.textContent).toBe('low25% · 1high75% · 3')
    expect([...view.querySelectorAll('.effort-mix-bar i')].map(i => i.className)).toEqual(['effort-tone-low', 'effort-tone-high'])
    expect([...view.querySelectorAll('.effort-mix-legend > span')].map(i => i.className)).toEqual(['effort-tone-low', 'effort-tone-high'])
    expect(view.querySelector('.effort-mix-head span')!.textContent).toBe('4 of 5 requests')
  })

  it('counts plainly when every request reported effort, in the singular for one', () => {
    const head = (mix: { effort: string | null; requests: number }[]) => {
      const text = render(createElement(EffortMixSection, { mix })).container.querySelector('.effort-mix-head span')!.textContent
      cleanup()
      return text
    }
    expect(head([{ effort: 'high', requests: 3 }, { effort: 'low', requests: 2 }])).toBe('5 requests')
    expect(head([{ effort: 'high', requests: 1 }])).toBe('1 request')
  })

  it('shows <1% for a small non-zero share and 0% for an empty one', () => {
    const view = render(createElement(EffortMixSection, { mix: [{ effort: 'high', requests: 999 }, { effort: 'low', requests: 1 }, { effort: 'max', requests: 0 }] })).container
    expect(view.querySelector('[role="img"]')!.getAttribute('aria-label')).toBe('low <1%, high 100%, max 0%')
    cleanup()
    expect(render(createElement(EffortMixSection, { mix: [{ effort: 'high', requests: 0 }] })).container.querySelector('[role="img"]')!.getAttribute('aria-label')).toBe('high 0%')
  })

  it('renders nothing when no request reported effort', () => {
    expect(render(createElement(EffortMixSection, { mix: [{ effort: null, requests: 14 }] })).container.innerHTML).toBe('')
  })
})
