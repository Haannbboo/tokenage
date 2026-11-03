import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type { SessionSummary, SessionDistribution, DateRangeOption, ActiveFilter } from '../types'
import { getSinceDate, rangeGranularity } from '../dateRange'
import { appendModels } from '../modelFilter'
import { getTimezoneOffset, resolveTimezone } from '../utils'
import { useApp } from '../contexts/AppContext'

export type SessionBucket = { kind: 'agent' | 'duration' | 'cost' | 'time'; key: string; start?: string; end?: string }
export function applySessionBucket(params: URLSearchParams, bucket: SessionBucket | null) {
  if (!bucket) return
  if (bucket.kind === 'agent') params.set('client_source', bucket.key)
  if (bucket.kind === 'duration') params.set('duration_bucket', bucket.key)
  if (bucket.kind === 'cost') params.set('cost_bucket', bucket.key)
  if (bucket.kind === 'time' && bucket.start && bucket.end) { params.set('since', bucket.start); params.set('until', bucket.end) }
}
export function sessionFilterQuery(source: string | null, device: string | null, filter: ActiveFilter, since: string | null) {
  const params = new URLSearchParams()
  if (source) params.set('client_source', source)
  if (device) params.set('device_id', device)
  if (filter?.provider) params.set('provider', filter.provider)
  appendModels(params, filter?.models)
  if (since) params.set('since', since)
  return params
}
export function useSessionsData(opts: { activeSource: string | null; dateRange: DateRangeOption; bucket: SessionBucket | null }) {
  const { refreshTrigger, activeDevice, activeFilter, timezone } = useApp()
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [distribution, setDistribution] = useState<SessionDistribution | null>(null)
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [distributionLoading, setDistributionLoading] = useState(true)
  const [sessionsError, setSessionsError] = useState(false)
  const [distributionError, setDistributionError] = useState(false)
  const [sessionSortBy, setSessionSortBy] = useState('ended')
  const [sessionSortOrder, setSessionSortOrder] = useState<'asc' | 'desc'>('desc')
  const [sessionPage, setSessionPage] = useState(1)
  const [hasMoreSessions, setHasMoreSessions] = useState(false)
  const since = useMemo(() => getSinceDate(opts.dateRange), [opts.dateRange, refreshTrigger])
  const query = sessionFilterQuery(opts.activeSource, activeDevice, activeFilter, since).toString()
  const bucketKey = JSON.stringify(opts.bucket)
  const handleSessionSort = useCallback((column: string) => {
    if (sessionSortBy === column) setSessionSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')
    else { setSessionSortBy(column); setSessionSortOrder('desc') }
    setSessionPage(1)
  }, [sessionSortBy])
  const previousFilter = useRef(`${query}|${bucketKey}`)
  useEffect(() => { setSessionPage(1); setSessions([]) }, [query, bucketKey])
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams(query)
    params.set('granularity', rangeGranularity(opts.dateRange))
    params.set('tz_offset', getTimezoneOffset(resolveTimezone(timezone)))
    setDistributionLoading(true); setDistributionError(false)
    fetch(`/sessions/distribution?${params}`, { signal: controller.signal }).then(r => { if (!r.ok) throw new Error(); return r.json() }).then(setDistribution).catch(() => { if (!controller.signal.aborted) { setDistribution(null); setDistributionError(true) } }).finally(() => { if (!controller.signal.aborted) setDistributionLoading(false) })
    return () => controller.abort()
  }, [query, opts.dateRange, timezone, refreshTrigger])
  useEffect(() => {
    const filterKey = `${query}|${bucketKey}`
    if (previousFilter.current !== filterKey) {
      previousFilter.current = filterKey
      if (sessionPage !== 1) return
    }
    const controller = new AbortController()
    const params = new URLSearchParams(query)
    applySessionBucket(params, JSON.parse(bucketKey))
    params.set('sort_by', sessionSortBy); params.set('sort_order', sessionSortOrder)
    params.set('limit', '50'); params.set('offset', String((sessionPage - 1) * 50))
    setSessionsLoading(true); setSessionsError(false)
    fetch(`/sessions?${params}`, { signal: controller.signal }).then(r => { if (!r.ok) throw new Error(); return r.json() }).then((data: { sessions: SessionSummary[] }) => {
      setSessions(prev => sessionPage === 1 ? data.sessions : [...prev, ...data.sessions]); setHasMoreSessions(data.sessions.length === 50)
    }).catch(() => { if (!controller.signal.aborted) setSessionsError(true) }).finally(() => { if (!controller.signal.aborted) setSessionsLoading(false) })
    return () => controller.abort()
  }, [query, bucketKey, sessionSortBy, sessionSortOrder, sessionPage, refreshTrigger])
  return { sessions, sessionsLoading, sessionsError, distribution, distributionLoading, distributionError, hasMoreSessions, sessionSortBy, sessionSortOrder, handleSessionSort, setSessionPage }
}
