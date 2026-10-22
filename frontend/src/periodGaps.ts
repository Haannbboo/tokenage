import type { DailyUsage } from './types'

const DAY_MS = 86400_000
const HOUR_MS = 3600_000

export function zeroUsageRow(period: string): DailyUsage {
  return {
    period, requests: 0, prompt_tokens: 0, completion_tokens: 0,
    cached_tokens: 0, cache_creation_tokens: 0, total_tokens: 0, input_cost_usd: 0,
    output_cost_usd: 0, total_cost_usd: 0, avg_latency_ms: 0,
    latency_sum_ms: 0,
    avg_throughput: 0,
    successful_requests: 0, failed_requests: 0,
    status_429: 0, status_4xx: 0, status_5xx: 0, status_unknown: 0,
  }
}

// The period is a wall-clock label in the display timezone ("2026-10-22" or
// "2026-10-22 14:00"), so it is read as UTC to keep the spacing exact.
export function periodToMs(period: string, granularity: 'hour' | 'day'): number | null {
  const [date, hour] = period.split(' ')
  const [y, m, d] = date.split('-').map(Number)
  if (!y || !m || !d) return null
  if (granularity === 'hour' && !hour) return null
  const h = hour ? Number(hour.split(':')[0]) : 0
  return Date.UTC(y, m - 1, d, h)
}

function msToPeriod(ms: number, granularity: 'hour' | 'day'): string {
  const iso = new Date(ms).toISOString()
  return granularity === 'hour'
    ? `${iso.slice(0, 10)} ${iso.slice(11, 13)}:00`
    : iso.slice(0, 10)
}

/**
 * Inserts the buckets the API left out: /usage/daily only returns periods that
 * have usage, so a day without traffic would otherwise be compressed out of
 * the series. Only the span between the first and the last period is filled,
 * so the result always stays inside the selected range.
 */
export function fillPeriodGaps(data: DailyUsage[], granularity: 'hour' | 'day'): DailyUsage[] {
  if (data.length < 2) return data

  const step = granularity === 'hour' ? HOUR_MS : DAY_MS
  const byKey = new Map<number, DailyUsage>()
  for (const row of data) {
    const key = periodToMs(row.period, granularity)
    if (key !== null) byKey.set(key, row)
  }
  if (byKey.size < 2) return data

  const keys = [...byKey.keys()]
  const first = Math.min(...keys)
  const last = Math.max(...keys)
  const buckets = Math.round((last - first) / step) + 1
  if (!Number.isFinite(buckets) || buckets > 4000) return data

  const result: DailyUsage[] = []
  for (let ms = first; ms <= last; ms += step) {
    result.push(byKey.get(ms) ?? zeroUsageRow(msToPeriod(ms, granularity)))
  }
  return result
}
