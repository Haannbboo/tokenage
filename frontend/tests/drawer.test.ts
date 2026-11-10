// @vitest-environment jsdom
import { createElement, useState } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Drawer } from '../src/components/Drawer'
import { AppProvider } from '../src/contexts/AppContext'

let phone = false
const listeners = new Set<() => void>()
function stubViewport(isPhone: boolean) {
  phone = isPhone
  vi.stubGlobal('matchMedia', () => ({
    get matches() { return phone },
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  }))
}
const rotate = (isPhone: boolean) => act(() => { phone = isPhone; listeners.forEach(fn => fn()) })

function Page({ selection = 'a', loaded = false }: { selection?: string; loaded?: boolean }) {
  const [open, setOpen] = useState(false)
  return createElement('div', null,
    createElement('nav', { 'aria-hidden': 'false' }, createElement('a', { href: '#x' }, 'Behind')),
    loaded && createElement('button', null, 'Outer background'),
    createElement('main', null,
      createElement('button', { onClick: () => setOpen(true) }, 'Open row'),
      loaded && createElement('button', null, 'Load more'),
      open && createElement(Drawer, { key: selection, title: 'Details', width: 480, onClose: () => setOpen(false) },
        createElement('a', { href: '#y' }, 'Inside link'))))
}
function openDrawer() {
  render(createElement(Page))
  const opener = screen.getByRole('button', { name: 'Open row' })
  opener.focus()
  fireEvent.click(opener)
  return { opener, drawer: screen.getByRole('dialog'), nav: document.querySelector('nav')! }
}
beforeEach(() => {
  // jsdom has no layout; model rectangles for visible elements only.
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(function (this: HTMLElement) {
    let visible = true
    for (let node: HTMLElement | null = this; node; node = node.parentElement) {
      if (node.hidden || node.style.display === 'none' || node.matches('input[type="hidden"]')) visible = false
    }
    return { length: visible ? 1 : 0 } as DOMRectList
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); listeners.clear() })

it('is a modal on phones: focus inside, Tab wraps, background inert, Escape restores focus', () => {
  stubViewport(true)
  const { opener, drawer, nav } = openDrawer()
  expect(drawer.getAttribute('aria-modal')).toBe('true')
  expect(document.activeElement).toBe(drawer)
  expect(nav.hasAttribute('inert')).toBe(true)
  expect(nav.getAttribute('aria-hidden')).toBe('true')
  expect(opener.hasAttribute('inert')).toBe(true)

  const close = screen.getByRole('button', { name: 'Close' })
  const inside = screen.getByRole('link', { name: 'Inside link' })
  fireEvent.keyDown(drawer, { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(inside)
  fireEvent.keyDown(inside, { key: 'Tab' })
  expect(document.activeElement).toBe(close)
  fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(inside)

  const restored = vi.fn(() => expect(opener.closest('[inert]')).toBeNull())
  opener.addEventListener('focus', restored)
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(nav.hasAttribute('inert')).toBe(false)
  expect(nav.getAttribute('aria-hidden')).toBe('false')
  expect(document.activeElement).toBe(opener)
  expect(restored).toHaveBeenCalledOnce()
})

it.each([false, true])('restores the original opener after a keyed swap (phone=%s)', isPhone => {
  stubViewport(isPhone)
  const view = render(createElement(Page, { selection: 'a' }))
  const opener = screen.getByRole('button', { name: 'Open row' })
  opener.focus()
  fireEvent.click(opener)
  const originalPanel = screen.getByRole('dialog')
  view.rerender(createElement(Page, { selection: 'b' }))
  const replacementPanel = screen.getByRole('dialog')
  expect(replacementPanel).not.toBe(originalPanel)
  expect(document.activeElement).toBe(replacementPanel)
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(document.activeElement).toBe(opener)
  expect(opener.closest('[inert]')).toBeNull()
})

it('inerts newly rendered siblings at each ancestor and restores them on close', async () => {
  stubViewport(true)
  const view = render(createElement(Page))
  const opener = screen.getByRole('button', { name: 'Open row' })
  opener.focus()
  fireEvent.click(opener)
  await act(async () => view.rerender(createElement(Page, { loaded: true })))
  const backgrounds = ['Load more', 'Outer background'].map(name => screen.getByText(name))
  for (const background of backgrounds) {
    expect(background.hasAttribute('inert')).toBe(true)
    expect(background.getAttribute('aria-hidden')).toBe('true')
  }
  fireEvent.keyDown(document, { key: 'Escape' })
  for (const background of backgrounds) {
    expect(background.hasAttribute('inert')).toBe(false)
    expect(background.hasAttribute('aria-hidden')).toBe(false)
  }
  const afterClose = document.createElement('button')
  backgrounds[0].parentElement!.append(afterClose)
  await act(async () => {})
  expect(afterClose.hasAttribute('inert')).toBe(false)
})

it('keeps the app toast container and other live regions reachable', async () => {
  stubViewport(true)
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ provider: 'local', user: null }) }))
  await act(async () => render(createElement(AppProvider, null,
    createElement(Page),
    createElement('div', { role: 'alert' }, 'Alert'),
    createElement('div', { role: 'log' }, 'Log'),
    createElement('div', { 'aria-live': 'assertive' }, 'Live'))))
  const opener = screen.getByRole('button', { name: 'Open row' })
  opener.focus()
  fireEvent.click(opener)
  const toast = screen.getByRole('status')
  expect(toast.classList.contains('toast-container')).toBe(true)
  expect(toast.getAttribute('aria-live')).toBe('polite')
  for (const region of [toast, screen.getByRole('alert'), screen.getByRole('log'), screen.getByText('Live')]) {
    expect(region.closest('[inert], [aria-hidden="true"]')).toBeNull()
  }
})

it('skips hidden and negative-tabindex candidates and wraps from unlisted focus', () => {
  stubViewport(true)
  const { drawer } = openDrawer()
  const close = screen.getByRole('button', { name: 'Close' })
  const inside = screen.getByRole('link', { name: 'Inside link' })
  const hidden = document.createElement('input')
  hidden.type = 'hidden'
  const displayNone = document.createElement('button')
  displayNone.style.display = 'none'
  const hiddenButton = document.createElement('button')
  hiddenButton.hidden = true
  const programmatic = document.createElement('button')
  programmatic.tabIndex = -2
  drawer.append(hidden, displayNone, hiddenButton, programmatic)
  close.focus()
  expect(fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })).toBe(false)
  expect(document.activeElement).toBe(inside)
  expect(fireEvent.keyDown(inside, { key: 'Tab' })).toBe(false)
  expect(document.activeElement).toBe(close)
  programmatic.focus()
  expect(fireEvent.keyDown(programmatic, { key: 'Tab' })).toBe(false)
  expect(document.activeElement).toBe(close)
  programmatic.focus()
  expect(fireEvent.keyDown(programmatic, { key: 'Tab', shiftKey: true })).toBe(false)
  expect(document.activeElement).toBe(inside)
})

it('stays a nonmodal side panel on desktop and switches when the viewport changes', () => {
  stubViewport(false)
  const { drawer, nav } = openDrawer()
  expect(drawer.hasAttribute('aria-modal')).toBe(false)
  expect(nav.hasAttribute('inert')).toBe(false)
  const inside = screen.getByRole('link', { name: 'Inside link' })
  inside.focus()
  const tab = fireEvent.keyDown(inside, { key: 'Tab' })
  expect(tab).toBe(true)  // not prevented: Tab leaves the drawer

  rotate(true)
  expect(nav.hasAttribute('inert')).toBe(true)
  expect(drawer.getAttribute('aria-modal')).toBe('true')
  expect(document.activeElement).toBe(inside)
  rotate(false)
  expect(nav.hasAttribute('inert')).toBe(false)
  expect(drawer.hasAttribute('aria-modal')).toBe(false)
})
