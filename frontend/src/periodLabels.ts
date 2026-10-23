import { periodToMs } from './periodGaps.ts'
import { t } from './i18n/index.ts'

const dayFormatters = new Map<string, Intl.DateTimeFormat>()
const timeFormatters = new Map<string, Intl.DateTimeFormat>()

function dayFormatter(locale: string) {
  let fmt = dayFormatters.get(locale)
  if (!fmt) {
    fmt = new Intl.DateTimeFormat(locale, {
      timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric',
    })
    dayFormatters.set(locale, fmt)
  }
  return fmt
}

function timeFormatter(locale: string) {
  let fmt = timeFormatters.get(locale)
  if (!fmt) {
    fmt = new Intl.DateTimeFormat(locale, {
      timeZone: 'UTC', hour: 'numeric', minute: '2-digit',
    })
    timeFormatters.set(locale, fmt)
  }
  return fmt
}

export type PeriodLabel = {
  /** Set when the bucket is today or yesterday. */
  relative?: 'today' | 'yesterday'
  /** e.g. "Tue, Oct 21" */
  day: string
  /** e.g. "1:00 PM", only for hourly buckets */
  time?: string
}

/**
 * Describes a period in words a person can read at a glance. The period is a
 * wall-clock label in the display timezone, so it is formatted in UTC to keep
 * the clock the one the data was bucketed by. `nowWallMs` is "now" in that
 * same wall clock.
 */
export function describePeriod(
  period: string,
  granularity: 'hour' | 'day',
  nowWallMs: number,
  locale: string,
): PeriodLabel | null {
  const ms = periodToMs(period, granularity)
  if (ms === null) return null

  const date = new Date(ms)
  const dayMs = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate())
  const now = new Date(nowWallMs)
  const todayMs = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  const dayDiff = Math.round((todayMs - dayMs) / 86400_000)

  const label: PeriodLabel = { day: dayFormatter(locale).format(date) }
  if (dayDiff === 0) label.relative = 'today'
  else if (dayDiff === 1) label.relative = 'yesterday'
  if (granularity === 'hour') label.time = timeFormatter(locale).format(date)
  return label
}

/** The label as a sentence: "Today 1:00 PM", "Yesterday", "Tue, Oct 21". */
export function periodLabelText(label: PeriodLabel | null): string | null {
  if (!label) return null
  if (label.relative === 'today') return `${t('Today')}${label.time ? ` ${label.time}` : ''}`
  if (label.relative === 'yesterday') return `${t('Yesterday')}${label.time ? ` ${label.time}` : ''}`
  return `${label.day}${label.time ? `, ${label.time}` : ''}`
}
