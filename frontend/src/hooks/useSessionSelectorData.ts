import { useEffect, useState } from 'react'
import type { DateRangeOption, SessionSelectorRow } from '../types'
import { getSinceDate } from '../dateRange.ts'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'

export function useSessionSelectorData(opts: {
  activeSource: string | null
  dateRange: DateRangeOption
}) {
  const { refreshTrigger, setError } = useApp()
  const [sessions, setSessions] = useState<SessionSelectorRow[]>([])

  useEffect(() => {
    const controller = new AbortController()
    const sig = { signal: controller.signal }
    setSessions([])

    async function fetchSessionSelectorData() {
      setError(null)
      try {
        const since = getSinceDate(opts.dateRange)
        const sessionsUrl = new URL('/sessions', window.location.origin)
        sessionsUrl.searchParams.set('view', 'selector')
        sessionsUrl.searchParams.set('sort_by', 'started')
        sessionsUrl.searchParams.set('sort_order', 'desc')
        sessionsUrl.searchParams.set('limit', '50')
        sessionsUrl.searchParams.set('offset', '0')
        if (opts.activeSource) sessionsUrl.searchParams.set('client_source', opts.activeSource)
        if (since) sessionsUrl.searchParams.set('since', since)

        const response = await fetch(sessionsUrl.toString(), sig)
        if (!response.ok) throw new Error(t('Failed to fetch session data'))
        const data: { sessions: SessionSelectorRow[] } = await response.json()
        setSessions(data.sessions)
      } catch (err) {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : t('Unknown error'))
      }
    }

    void fetchSessionSelectorData()
    return () => controller.abort()
  }, [opts.activeSource, opts.dateRange, refreshTrigger, setError])

  return { sessions }
}
