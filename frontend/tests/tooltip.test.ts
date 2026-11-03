// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { installTooltips, tooltipPosition } from '../src/tooltip'

const rect = (left: number, top: number, width: number, height: number) => ({
  left, top, width, height, right: left + width, bottom: top + height,
} as DOMRect)

describe('tooltip positioning', () => {
  it('opens above when there is room', () => {
    expect(tooltipPosition(rect(300, 400, 40, 20), rect(0, 0, 200, 240), 800, 600))
      .toEqual({ left: 220, top: 152, below: false, arrow: 100, maxHeight: 388 })
  })

  it('opens below and clamps to the right edge when needed', () => {
    expect(tooltipPosition(rect(350, 100, 30, 20), rect(0, 0, 200, 240), 390, 600))
      .toEqual({ left: 186, top: 128, below: true, arrow: 179, maxHeight: 468 })
  })

  it('caps height below when neither side fits', () => {
    expect(tooltipPosition(rect(0, 130, 10, 20), rect(0, 0, 200, 240), 390, 350))
      .toEqual({ left: 4, top: 158, below: true, arrow: 8, maxHeight: 188 })
  })

  it('caps height above when it has more room', () => {
    expect(tooltipPosition(rect(0, 200, 10, 20), rect(0, 0, 200, 240), 390, 350))
      .toEqual({ left: 4, top: 4, below: false, arrow: 8, maxHeight: 188 })
  })
})

it('leaves the CSS tooltip alone when the Popover API is missing', () => {
  expect('showPopover' in HTMLElement.prototype).toBe(false)
  const add = vi.spyOn(document, 'addEventListener')
  installTooltips()()
  expect(add).not.toHaveBeenCalled()
  add.mockRestore()
})

it('shows, repositions and hides a popover, then removes listeners', () => {
  let open = false
  let resize: ResizeObserverCallback = () => {}
  const disconnect = vi.fn()
  const observe = vi.fn()
  const show = vi.fn(() => { open = true })
  const hide = vi.fn(() => { open = false })
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { resize = callback }
    observe = observe
    disconnect = disconnect
  })
  Object.defineProperty(HTMLElement.prototype, 'showPopover', { configurable: true, value: show })
  Object.defineProperty(HTMLElement.prototype, 'hidePopover', { configurable: true, value: hide })
  const matches = vi.spyOn(HTMLElement.prototype, 'matches').mockImplementation(function (this: HTMLElement, selector) {
    return selector === ':popover-open' ? open : Element.prototype.matches.call(this, selector)
  })
  const docRemove = vi.spyOn(document, 'removeEventListener')
  const winRemove = vi.spyOn(window, 'removeEventListener')
  const host = document.createElement('div')
  host.className = 'has-tooltip session-tools-more'
  host.innerHTML = '<button>+1</button><div class="tooltip-text">Cost</div>'
  document.body.append(host)
  const content = host.querySelector<HTMLElement>('.tooltip-text')!
  const button = host.querySelector('button')!
  const bounds = vi.spyOn(host, 'getBoundingClientRect').mockReturnValue(rect(20, 100, 20, 20))
  const uninstall = installTooltips()
  try {
    host.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
    expect(show).toHaveBeenCalledOnce()
    expect(observe).toHaveBeenCalledWith(content)
    bounds.mockReturnValue(rect(20, 200, 20, 20))
    resize([], {} as ResizeObserver)
    expect(content.style.getPropertyValue('--tooltip-top')).toBe('192px')
    host.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }))
    expect(hide).toHaveBeenCalledOnce()
    expect(disconnect).toHaveBeenCalledOnce()
    button.dispatchEvent(new FocusEvent('focusin', { bubbles: true }))
    expect(show).toHaveBeenCalledTimes(2)
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(hide).toHaveBeenCalledOnce()
    button.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
    expect(hide).toHaveBeenCalledTimes(2)
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    expect(show).toHaveBeenCalledTimes(3)
    host.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }))
    expect(hide).toHaveBeenCalledTimes(3)
    uninstall()
    expect(docRemove.mock.calls.map(call => call[0])).toEqual(expect.arrayContaining(['mouseover', 'mouseout', 'wheel']))
    expect(winRemove.mock.calls.map(call => call[0])).toEqual(expect.arrayContaining(['scroll', 'resize']))
  } finally {
    uninstall()
    host.remove()
    matches.mockRestore()
    docRemove.mockRestore()
    winRemove.mockRestore()
    delete (HTMLElement.prototype as Partial<HTMLElement>).showPopover
    delete (HTMLElement.prototype as Partial<HTMLElement>).hidePopover
    vi.unstubAllGlobals()
  }
})
