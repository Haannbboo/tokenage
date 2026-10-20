import { useState, useEffect, useCallback, useRef } from 'react'
import type { UsageRow, DateRangeOption, ActiveFilter } from '../types'
import type { RequestLogColumnId } from './useRequestLogColumns'
import { customBound, getSinceDate } from '../utils'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'

const DEFAULT_COL_WIDTHS: Record<RequestLogColumnId, number> = {
  time: 120,
  model: 135,
  provider: 120,
  source: 110,
  device: 120,
  session: 120,
  input: 220,
  output: 130,
  cost: 80,
  speed: 110,
  status: 80,
  tool: 100,
}

export function useLogsData(opts: {
  activeFilter: ActiveFilter
  activeSource: string | null
  sessionFilter: string | null
  toolFilter: string | null
  deviceFilter: string | null
  dateRange: DateRangeOption
  customSince: string
  customUntil: string
}) {
  const { refreshTrigger, setError } = useApp()

  const [usageRows, setUsageRows] = useState<UsageRow[]>([])
  const [totalLogs, setTotalLogs] = useState(0)
  const [limit, setLimit] = useState(10)
  const [page, setPage] = useState(1)
  const [jumpPage, setJumpPage] = useState('')
  const [logsLoading, setLogsLoading] = useState(true)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [colWidths, setColWidths] = useState<Record<RequestLogColumnId, number>>(DEFAULT_COL_WIDTHS)
  const [resizedColumns, setResizedColumns] = useState<Set<RequestLogColumnId>>(new Set())
  const resizeRef = useRef<{ columnId: RequestLogColumnId; startX: number; startWidth: number } | null>(null)

  const resetPage = useCallback(() => setPage(1), [])

  const handleResizeStart = (columnId: RequestLogColumnId, e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const thEl = (e.currentTarget as HTMLElement).closest('th')
    const startWidth = thEl?.offsetWidth ?? colWidths[columnId]
    resizeRef.current = { columnId, startX, startWidth }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    const handleMouseMove = (e: MouseEvent) => {
      const delta = e.clientX - startX
      const newWidth = Math.max(80, startWidth + delta)
      setColWidths(prev => ({ ...prev, [columnId]: newWidth }))
      setResizedColumns(prev => prev.has(columnId) ? prev : new Set(prev).add(columnId))
    }
    const handleMouseUp = () => {
      resizeRef.current = null
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
  }

  // Logs fetch
  useEffect(() => {
    const controller = new AbortController()
    const sig = { signal: controller.signal }

    async function fetchLogs() {
      setError(null)
      setLogsLoading(true)
      try {
        const since = opts.dateRange === 'custom' ? customBound(opts.customSince) : getSinceDate(opts.dateRange)
        const until = opts.dateRange === 'custom' ? customBound(opts.customUntil) : null

        const usageUrl = new URL('/usage', window.location.origin)
        usageUrl.searchParams.set('limit', String(limit))
        usageUrl.searchParams.set('offset', String((page - 1) * limit))
        if (opts.activeFilter) {
          if (opts.activeFilter.provider) usageUrl.searchParams.set('provider', opts.activeFilter.provider)
          if (opts.activeFilter.model) usageUrl.searchParams.set('model', opts.activeFilter.model)
        }
        if (opts.activeSource) usageUrl.searchParams.set('client_source', opts.activeSource)
        if (opts.sessionFilter) usageUrl.searchParams.set('session_id', opts.sessionFilter)
        if (opts.toolFilter) usageUrl.searchParams.set('tool_name', opts.toolFilter)
        if (opts.deviceFilter) usageUrl.searchParams.set('device_id', opts.deviceFilter)
        if (since) usageUrl.searchParams.set('since', since)
        if (until) usageUrl.searchParams.set('until', until)

        const countUrl = new URL('/usage/count', window.location.origin)
        if (opts.activeFilter) {
          if (opts.activeFilter.provider) countUrl.searchParams.set('provider', opts.activeFilter.provider)
          if (opts.activeFilter.model) countUrl.searchParams.set('model', opts.activeFilter.model)
        }
        if (opts.activeSource) countUrl.searchParams.set('client_source', opts.activeSource)
        if (opts.sessionFilter) countUrl.searchParams.set('session_id', opts.sessionFilter)
        if (opts.toolFilter) countUrl.searchParams.set('tool_name', opts.toolFilter)
        if (opts.deviceFilter) countUrl.searchParams.set('device_id', opts.deviceFilter)
        if (since) countUrl.searchParams.set('since', since)
        if (until) countUrl.searchParams.set('until', until)

        const responses = await Promise.all([
          fetch(usageUrl.toString(), sig),
          fetch(countUrl.toString(), sig),
        ])

        if (responses.some(r => !r.ok)) throw new Error(t('Failed to fetch log data'))
        const [usageData, countData] =
          await Promise.all(responses.map(r => r.json())) as [UsageRow[], { total: number }]

        setUsageRows(usageData)
        setTotalLogs(countData.total)
      } catch (err) {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : t('Unknown error'))
      } finally {
        setLogsLoading(false)
      }
    }

    void fetchLogs()
    return () => controller.abort()
  }, [opts.activeFilter, opts.activeSource, opts.sessionFilter, opts.toolFilter, opts.deviceFilter, opts.dateRange, opts.customSince, opts.customUntil, limit, page, refreshTrigger, setError])

  const totalPages = Math.ceil(totalLogs / limit)

  return {
    usageRows, setUsageRows, totalLogs, totalPages,
    limit, setLimit, page, setPage, jumpPage, setJumpPage, resetPage,
    logsLoading, expandedRow, setExpandedRow,
    colWidths, resizedColumns, handleResizeStart,
  }
}
