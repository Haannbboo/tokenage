import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import type { SessionSummary, DateRangeOption } from '../types'
import { buildSessionInsights } from '../utils'
import { getSinceDate } from '../dateRange.ts'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'

export function useSessionsData(opts: {
  activeSource: string | null
  dateRange: DateRangeOption
  hideNoop?: boolean
}) {
  const { refreshTrigger, setError } = useApp()

  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(true)
  const [sessionSortBy, setSessionSortBy] = useState<string>('started')
  const [sessionSortOrder, setSessionSortOrder] = useState<'asc' | 'desc'>('desc')
  const [sessionPage, setSessionPage] = useState(1)
  const [hasMoreSessions, setHasMoreSessions] = useState(true)
  const [selectedSession, setSelectedSession] = useState<SessionSummary | null>(null)

  const prevFilterKeyRef = useRef('')
  const sinceRef = useRef<string | null>(null)

  const handleSessionSort = useCallback((column: string) => {
    if (sessionSortBy === column) {
      setSessionSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')
    } else {
      setSessionSortBy(column)
      setSessionSortOrder('desc')
    }
    setSessionPage(1)
  }, [sessionSortBy])

  const sessionInsights = useMemo(() => {
    return buildSessionInsights(sessions)
  }, [sessions])

  // Filter-change keys — when any of these change, reset to page 1
  const filterKey = `${opts.activeSource}|${opts.dateRange}|${opts.hideNoop}|${refreshTrigger}`

  // Sessions fetch
  useEffect(() => {
    const controller = new AbortController()
    const sig = { signal: controller.signal }

    // When filters change, React batches all state updates in this effect.
    // Reset page to 1 first — subsequent re-renders with the new page value
    // will re-trigger this effect via the sessionPage dependency.
    const filterChanged = prevFilterKeyRef.current !== filterKey
    if (filterChanged) {
      prevFilterKeyRef.current = filterKey
      if (sessionPage !== 1) {
        setSessionPage(1)
        return () => controller.abort()
      }
      // Page is already 1 — clear stale sessions so the table doesn't flash old data
      setSessions([])
    }

    async function fetchSessionsData() {
      setError(null)
      setSessionsLoading(true)
      try {
        if (sessionPage === 1) sinceRef.current = getSinceDate(opts.dateRange)
        const since = sinceRef.current

        const sessionsUrl = new URL('/sessions', window.location.origin)
        if (opts.activeSource) sessionsUrl.searchParams.set('client_source', opts.activeSource)
        if (since) sessionsUrl.searchParams.set('since', since)
        sessionsUrl.searchParams.set('sort_by', sessionSortBy)
        sessionsUrl.searchParams.set('sort_order', sessionSortOrder)
        sessionsUrl.searchParams.set('limit', '50')
        sessionsUrl.searchParams.set('offset', String((sessionPage - 1) * 50))
        if (opts.hideNoop) sessionsUrl.searchParams.set('hide_noop', 'true')

        const response = await fetch(sessionsUrl.toString(), sig)
        if (!response.ok) throw new Error(t('Failed to fetch session data'))
        const sessionsData: { sessions: SessionSummary[] } = await response.json()

        if (sessionPage === 1) {
          setSessions(sessionsData.sessions)
        } else {
          setSessions(prev => [...prev, ...sessionsData.sessions])
        }
        setHasMoreSessions(sessionsData.sessions.length === 50)
      } catch (err) {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : t('Unknown error'))
      } finally {
        setSessionsLoading(false)
      }
    }

    void fetchSessionsData()
    return () => controller.abort()
  }, [opts.activeSource, opts.dateRange, opts.hideNoop, refreshTrigger, filterKey, sessionSortBy, sessionSortOrder, sessionPage, setError])

  return {
    sessions,
    setSessions,
    sessionsLoading,
    hasMoreSessions,
    sessionSortBy,
    sessionSortOrder,
    selectedSession,
    setSelectedSession,
    handleSessionSort,
    sessionInsights,
    setSessionPage,
  }
}
