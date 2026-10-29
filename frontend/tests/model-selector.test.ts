// @vitest-environment jsdom
import { createElement, useState } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ModelSelector } from '../src/ModelSelector'
import type { ActiveFilter, UsageSummary } from '../src/types'

const summary = [
  { provider: 'openai', model: 'gpt-alpha' },
  { provider: 'openai', model: 'gpt-beta' },
  { provider: 'anthropic', model: 'claude-gamma' },
] as UsageSummary[]
function Harness() {
  const [filter, setFilter] = useState<ActiveFilter>(null)
  return createElement(ModelSelector, { summary, activeFilter: filter, onChange: setFilter, providerColors: {} })
}
afterEach(cleanup)
it('searches providers and models, toggles selections, and selects a provider', () => {
  render(createElement(Harness))
  fireEvent.click(screen.getByRole('button', { name: /all models/i }))
  const search = screen.getByRole('textbox', { name: /search models/i })
  expect(document.activeElement).toBe(search)
  fireEvent.change(search, { target: { value: 'ANTHROPIC' } })
  expect(screen.getAllByRole('menuitemcheckbox')).toHaveLength(2)
  fireEvent.change(search, { target: { value: 'beta' } })
  expect(screen.getAllByRole('menuitemcheckbox')).toHaveLength(2)
  fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /gpt-beta/i }))
  fireEvent.change(search, { target: { value: '' } })
  fireEvent.click(screen.getByRole('menuitemcheckbox', { name: /claude-gamma/i }))
  expect(screen.getByRole('button', { name: /2 models/i })).toBeTruthy()
  const openai = screen.getByRole('menuitemcheckbox', { name: /openai/i })
  expect(openai.getAttribute('aria-checked')).toBe('mixed')
  expect(openai.textContent).not.toMatch(/\d/)
  fireEvent.click(openai)
  expect(screen.getByRole('button', { name: /3 models/i })).toBeTruthy()
  fireEvent.click(openai)
  expect(screen.getByRole('menuitemcheckbox', { name: /gpt-beta/i }).getAttribute('aria-checked')).toBe('false')
  fireEvent.click(screen.getByRole('button', { name: /clear all/i }))
  expect(within(screen.getByRole('menu')).getAllByRole('menuitemcheckbox').every(row => row.getAttribute('aria-checked') === 'false')).toBe(true)
  fireEvent.keyDown(search, { key: 'ArrowDown' })
  expect(document.activeElement?.getAttribute('role')).toBe('menuitemcheckbox')
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
  expect(screen.queryByRole('menu')).toBeNull()
})

it('serializes each selected model as a separate query parameter', async () => {
  const { appendModels } = await import('../src/modelFilter')
  const params = new URLSearchParams()
  appendModels(params, ['gpt-alpha', 'vendor/model,variant'])
  expect(new URLSearchParams(params.toString()).getAll('model')).toEqual(['gpt-alpha', 'vendor/model,variant'])
})


it('aligns the popover to the trigger and keeps it inside the viewport on resize', () => {
  let triggerLeft = 100
  let triggerWidth = 180
  let popoverWidth = 340
  const rect = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const isTrigger = this.classList.contains('model-selector-trigger')
    const left = isTrigger ? triggerLeft : 0
    const width = isTrigger ? triggerWidth : popoverWidth
    return { left, right: left + width, width, top: 0, bottom: 0, height: 0, x: left, y: 0, toJSON: () => ({}) }
  })
  const originalWidth = window.innerWidth
  try {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1000 })
    render(createElement(Harness))
    fireEvent.click(screen.getByRole('button', { name: /all models/i }))
    const popover = screen.getByRole('menu').parentElement!
    expect(popover.style.left).toBe('0px')
    triggerLeft = 800
    fireEvent.resize(window)
    expect(popover.style.left).toBe('-160px')
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 320 })
    triggerLeft = 20
    triggerWidth = 140
    popoverWidth = 296
    fireEvent.resize(window)
    expect(popover.style.left).toBe('-8px')
  } finally {
    rect.mockRestore()
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
  }
})
