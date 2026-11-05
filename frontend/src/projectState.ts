import { rangePeriodCount } from './dateRange'
import type { DateRangeOption, ProjectBucket } from './types'

/** Preserve other filters while opening/closing a URL-addressable detail. */
export function detailSearch(current: URLSearchParams, key: 'project' | 'session', value: string | null) {
  const next = new URLSearchParams(current)
  if (value === null) next.delete(key)
  else next.set(key, value)
  return next
}
export function projectSessionsUrl(project: string, range: DateRangeOption, session?: string) {
  const params = new URLSearchParams({ project, range })
  if (session) params.set('session', session)
  return `/sessions?${params}`
}
export function projectStackMaximum(buckets: ProjectBucket[]) {
  return Math.max(1, ...buckets.map(bucket => Object.values(bucket.by_project).reduce((sum, count) => sum + count, 0)))
}
export function costPerSession(cost: number, sessions: number) { return sessions > 0 ? cost / sessions : 0 }
export function projectAverageLatency(latencySum: number, requests: number) { return requests > 0 ? latencySum / requests : 0 }

/** Match the other charts: discard the extra leading partial period. */
export function projectBuckets(buckets: ProjectBucket[], range: DateRangeOption) {
  const count = rangePeriodCount(range)
  return count ? buckets.slice(-count) : buckets
}
