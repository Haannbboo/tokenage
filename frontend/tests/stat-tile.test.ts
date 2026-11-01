// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { StatTile } from '../src/components/StatTile'

afterEach(cleanup)
it('marks a zero value as lowest when its bucket is active', () => {
  render(createElement(StatTile, {
    label: 'Cache', icon: '', iconClass: '', color: 'red',
    points: [{ value: 0, period: 'active', active: true }, { value: 50, period: 'other', active: true }, { value: 0, period: 'idle', active: false }],
    animatedValue: 0, format: String, cursorIdx: 0, onHover: () => {},
  }))
  expect(screen.getByText('active · lowest')).toBeTruthy()
})
