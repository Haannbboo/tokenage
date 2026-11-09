import { useEffect, useMemo, useState } from 'react'
import { useApp } from '../contexts/AppContext'
import { getSinceDate } from '../dateRange'
import { sessionFilterQuery } from './useSessionsData'
import { getTimezoneOffset, resolveTimezone } from '../utils'
import type { DateRangeOption, ProjectDetail, ProjectsResponse } from '../types'

function useProjectRead<T>(url: string | null, refresh: number, resource: string | null) {
  const [state, setState] = useState<{ resource: string | null; data: T | null; loading: boolean; error: boolean }>({ resource, data: null, loading: !!url, error: false })
  useEffect(() => {
    if (!url) { setState({ resource, data: null, loading: false, error: false }); return }
    const controller = new AbortController()
    setState(previous => ({ resource, data: previous.resource === resource ? previous.data : null, loading: true, error: false }))
    fetch(url, { signal: controller.signal }).then(response => { if (!response.ok) throw new Error(); return response.json() }).then(data => {
      if (!controller.signal.aborted) setState({ resource, data, loading: false, error: false })
    }).catch(() => { if (!controller.signal.aborted) setState(previous => ({ ...previous, loading: false, error: true })) })
    return () => controller.abort()
  }, [url, refresh, resource])
  return { ...state, data: state.resource === resource ? state.data : null, loading: state.loading || (state.resource !== resource && !!url) }
}
export function useProjectsData(project: string | null, dateRange: DateRangeOption) {
  const { activeSource, activeDevice, activeFilter, timezone, refreshTrigger } = useApp()
  const since = useMemo(() => getSinceDate(dateRange), [dateRange, refreshTrigger])
  const params = sessionFilterQuery(activeSource, activeDevice, activeFilter, since)
  // The project chart and activity tiles measure days, including partial days.
  params.set('granularity', 'day')
  params.set('tz_offset', getTimezoneOffset(resolveTimezone(timezone)))
  const query = params.toString()
  // Cache key: the filters, with the range name instead of `since`, which moves on every refresh.
  const filters = new URLSearchParams(params)
  filters.set('since', dateRange)
  const key = filters.toString()
  const list = useProjectRead<ProjectsResponse>(`/sessions/projects?${query}`, refreshTrigger, key)
  if (project !== null) params.set('name', project)
  const detail = useProjectRead<ProjectDetail>(project === null ? null : `/sessions/projects/detail?${params}`, refreshTrigger, project === null ? null : `${key}\n${project}`)
  return { list, detail }
}
