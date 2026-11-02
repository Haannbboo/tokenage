// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { UserAvatar } from '../src/components/UserAvatar'
import type { AuthUser } from '../src/types'

afterEach(cleanup)

const user = (picture?: string) => ({ email: 'alice@example.com', name: 'Alice', picture }) as AuthUser

test('falls back to the initial when the picture fails, and retries a new picture', () => {
  const view = render(createElement(UserAvatar, { user: user('https://a.test/1.png') }))
  fireEvent.error(view.container.querySelector('img')!)
  expect(view.container.querySelector('img')).toBeNull()
  expect(view.container.textContent).toBe('A')

  view.rerender(createElement(UserAvatar, { user: user('https://a.test/2.png') }))
  expect(view.container.querySelector('img')?.getAttribute('src')).toBe('https://a.test/2.png')
})

test('shows the initial without a picture', () => {
  const view = render(createElement(UserAvatar, { user: user() }))
  expect(view.container.querySelector('img')).toBeNull()
  expect(view.container.textContent).toBe('A')
})
