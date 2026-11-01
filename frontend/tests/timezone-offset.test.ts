// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { getTimezoneOffset } from '../src/utils'

afterEach(() => vi.useRealTimers())
it('preserves offsets east of UTC+12', () => {
  vi.useFakeTimers().setSystemTime(new Date('2026-01-15T12:00:00Z'))
  expect(getTimezoneOffset('Pacific/Auckland')).toBe('+13:00')
  expect(getTimezoneOffset('Pacific/Kiritimati')).toBe('+14:00')
})
