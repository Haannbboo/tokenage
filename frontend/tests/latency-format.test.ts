// @vitest-environment jsdom
import { expect, it } from 'vitest'
import { formatLatency } from '../src/utils'

it('distinguishes missing latency from zero and positive values', () => {
  expect(formatLatency(null)).toBe('—')
  expect(formatLatency(undefined)).toBe('—')
  expect(formatLatency(0)).toBe('0ms')
  expect(formatLatency(250)).toBe('250ms')
  expect(formatLatency(1250)).toBe('1.25s')
})
