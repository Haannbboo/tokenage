import { describe, expect, it } from 'vitest'
import { DATE_RANGE_OPTIONS, getSinceDate, rangeGranularity, rangePeriodCount } from '../src/dateRange'

describe('date range options', () => {
  it('offers the short ranges first and all time last', () => {
    expect(DATE_RANGE_OPTIONS.map(o => o.value)).toEqual(['5h', '24h', '7d', '30d', 'all'])
    for (const option of DATE_RANGE_OPTIONS) expect(option.label.length).toBeGreaterThan(0)
  })

  it('leaves all time without a lower bound', () => {
    expect(getSinceDate('all')).toBeNull()
  })

  it('starts the short ranges hours ago', () => {
    const hours = (option: '5h' | '24h') => {
      const since = getSinceDate(option)
      expect(since).toBeTruthy()
      return Math.round((Date.now() - new Date(since!).getTime()) / 3600_000)
    }
    expect(hours('24h')).toBe(24)
    expect(hours('5h')).toBe(5)
    expect(new Date(getSinceDate('5h')!).getTime() % (15 * 60_000)).toBe(0)
  })

  it('starts the day ranges days ago', () => {
    const days = (option: '7d' | '30d') => {
      const since = getSinceDate(option)
      expect(since).toBeTruthy()
      return Math.round((Date.now() - new Date(since!).getTime()) / 86400_000)
    }
    expect(days('7d')).toBe(7)
    expect(days('30d')).toBe(30)
  })

  it('draws the axis finer than the rollup for the five hour range', () => {
    expect(rangeGranularity('5h')).toBe('15m')
    expect(rangeGranularity('24h')).toBe('hour')
    expect(rangeGranularity('7d')).toBe('day')
    expect(rangeGranularity('30d')).toBe('day')
    expect(rangeGranularity('all')).toBe('day')
  })

  it('gives the trend chart one period per bucket', () => {
    expect(rangePeriodCount('24h')).toBe(24)
    expect(rangePeriodCount('5h')).toBe(21)
    expect(rangePeriodCount('7d')).toBe(7)
    expect(rangePeriodCount('30d')).toBe(30)
    expect(rangePeriodCount('all')).toBeUndefined()
  })
})
