// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ClickToCopy } from '../src/components/CopyButton'
import { expandableRowProps } from '../src/expandableRow'

afterEach(cleanup)

it('copies from the keyboard without toggling the row it sits in', () => {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
  const toggle = vi.fn()
  render(createElement('table', null, createElement('tbody', null,
    createElement('tr', expandableRowProps(true, toggle), createElement('td', null,
      createElement(ClickToCopy, { text: 'req-123' }, 'req-1…'))))))
  const copy = screen.getByRole('button', { name: 'req-1…' })
  expect(copy.tabIndex).toBe(0)
  fireEvent.keyDown(copy, { key: 'Enter' })
  fireEvent.keyDown(copy, { key: ' ' })
  fireEvent.click(copy)
  expect(writeText).toHaveBeenCalledTimes(3)
  expect(writeText).toHaveBeenCalledWith('req-123')
  expect(toggle).not.toHaveBeenCalled()
  fireEvent.keyDown(copy, { key: 'a' })
  expect(writeText).toHaveBeenCalledTimes(3)
})
