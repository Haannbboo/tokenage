// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { installTooltips, tooltipPosition } from '../src/tooltip'

const rect = (left: number, top: number, width: number, height: number) => ({
  left, top, width, height, right: left + width, bottom: top + height,
} as DOMRect)

describe('tooltip positioning', () => {
  it('opens above when there is room', () => {
    expect(tooltipPosition(rect(300, 400, 40, 20), rect(0, 0, 200, 240), 800, 600))
      .toEqual({ left: 220, top: 152, below: false, arrow: 100 })
  })

  it('opens below and clamps to the right edge when needed', () => {
    expect(tooltipPosition(rect(350, 100, 30, 20), rect(0, 0, 200, 240), 390, 600))
      .toEqual({ left: 186, top: 128, below: true, arrow: 179 })
  })

  it('clamps against the bottom and left edges', () => {
    expect(tooltipPosition(rect(0, 130, 10, 20), rect(0, 0, 200, 240), 390, 350))
      .toEqual({ left: 4, top: 106, below: true, arrow: 8 })
  })
})

it('leaves the CSS tooltip alone when the Popover API is missing', () => {
  expect('showPopover' in HTMLElement.prototype).toBe(false)
  const add = vi.spyOn(document, 'addEventListener')
  installTooltips()()
  expect(add).not.toHaveBeenCalled()
  add.mockRestore()
})
