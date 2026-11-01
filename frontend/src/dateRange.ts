import type { DateRangeOption, PeriodGranularity } from './types'

const HOUR_MS = 3600_000
const DAY_MS = 86400_000

/** The ranges the toolbars offer, shortest first. Labels are translation keys. */
export const DATE_RANGE_OPTIONS: { value: DateRangeOption; label: string }[] = [
  { value: '5h', label: 'Last 5 Hours' },
  { value: '24h', label: 'Last 24 Hours' },
  { value: '7d', label: 'Last 7 Days' },
  { value: '30d', label: 'Last 30 Days' },
  { value: 'all', label: 'All Time' },
]

/** The start of the window, or null when the range has no lower bound. */
export function getSinceDate(option: DateRangeOption): string | null {
  if (option === 'all') return null
  if (option === '5h') return new Date(Math.floor((Date.now() - 5 * HOUR_MS) / (15 * 60_000)) * (15 * 60_000)).toISOString()
  if (option === '24h') return new Date(Date.now() - 24 * HOUR_MS).toISOString()
  if (option === '7d') return new Date(Date.now() - 7 * DAY_MS).toISOString()
  return new Date(Date.now() - 30 * DAY_MS).toISOString()
}

/**
 * The bucket of a range: what the API is asked for and what the chart is
 * drawn in. The five hour range is finer than the hourly rollup, so the API
 * sums the raw rows for it.
 */
export function rangeGranularity(option: DateRangeOption): PeriodGranularity {
  if (option === '5h') return '15m'
  if (option === '24h') return 'hour'
  return 'day'
}

/** How many periods the trend chart shows; open ended for all time. */
export function rangePeriodCount(option: DateRangeOption): number | undefined {
  if (option === '24h') return 24
  if (option === '5h') return 21
  if (option === '7d') return 7
  if (option === '30d') return 30
  return undefined
}
