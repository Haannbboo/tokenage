import { useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef } from 'react'
import type { UsageRow, ActiveFilter } from '../types'
import type { RequestLogColumnId } from './useRequestLogColumns'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'

// The log table uses `table-layout: fixed` so the virtualized rows keep one
// stable column grid while scrolling. Fixed layout honours `width` only —
// `min-width` is ignored, and a column without a width silently splits the
// leftover space equally with its neighbours. So every column needs an
// explicit width, and the widths are proportions: the table scales them to
// fill its width.
//
// Input and Output carry the dense content (token counts, cache read/write
// percentages, a proportional bar), so they get the room; the number-only
// columns stay narrow. The total leaves a little slack so dragging a column
// wider does not immediately drop the lowest-priority ones.
export const DEFAULT_COL_WIDTHS: Record<RequestLogColumnId, number> = {
  time: 112,
  model: 135,
  provider: 112,
  source: 88,
  device: 95,
  session: 100,
  input: 245,
  output: 190,
  cost: 75,
  tool: 145,
  speed: 78,
  status: 70,
}

export const LOGS_PAGE_SIZE = 100
const cursorFor = (row: UsageRow) => `${row.ts}:${row.id}`

export function useLogsData(opts: {
  activeFilter: ActiveFilter
  activeSource: string | null
  sessionFilter: string | null
  toolFilter: string | null
  deviceFilter: string | null
}) {
  const { refreshTrigger, setError } = useApp()

  const [usageRows, setUsageRows] = useState<UsageRow[]>([])
  const [hasMore, setHasMore] = useState(true)
  const [loadMoreFailed, setLoadMoreFailed] = useState(false)
  const [initialLoadFailed, setInitialLoadFailed] = useState(false)
  const [scrollReset, setScrollReset] = useState(0)
  const rowsRef = useRef(usageRows)
  const requestRef = useRef<AbortController | null>(null)
  const busyRef = useRef(false)
  const filterRef = useRef<string | null>(null)
  useLayoutEffect(() => { rowsRef.current = usageRows }, [usageRows])
  const [logsLoading, setLogsLoading] = useState(true)
  const [expandedRow, setExpandedRow] = useState<string | null>(null)
  const [colWidths, setColWidths] = useState<Record<RequestLogColumnId, number>>(DEFAULT_COL_WIDTHS)
  const [isResizing, setIsResizing] = useState(false)
  const resizeRef = useRef<{
    columnId: RequestLogColumnId
    startX: number
    /** The column's rendered width when the drag started. */
    startWidth: number
    table: HTMLTableElement | null
    th: HTMLTableCellElement | null
  } | null>(null)


  const handleResizeStart = (columnId: RequestLogColumnId, e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const thEl = (e.currentTarget as HTMLElement).closest('th')
    setIsResizing(true)
    resizeRef.current = {
      columnId,
      startX,
      startWidth: thEl?.offsetWidth ?? colWidths[columnId],
      table: thEl?.closest('table') ?? null,
      th: thEl ?? null,
    }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    const handleMouseMove = (e: MouseEvent) => {
      const state = resizeRef.current
      if (!state) return
      const desired = Math.max(80, state.startWidth + (e.clientX - state.startX))
      setColWidths(prev => {
        // Fixed layout scales the columns up to fill the table, so a rendered
        // pixel is worth less than a specified pixel. Measure the live ratio
        // and solve for the width that renders at the dragged size.
        const spec = prev[state.columnId]
        const rendered = state.th?.offsetWidth ?? spec
        const perSpec = spec > 0 && rendered > 0 ? rendered / spec : 1
        const othersRendered = state.table
          ? [...state.table.querySelectorAll('thead th')]
            .filter(th => th !== state.th)
            .reduce((sum, th) => sum + (th as HTMLElement).offsetWidth, 0)
          : 0
        const othersSpec = perSpec > 0 ? othersRendered / perSpec : 0
        const tableWidth = state.table?.parentElement?.clientWidth ?? 0
        // Fits the table: rendered = spec * tableWidth / (othersSpec + spec).
        const scaled = tableWidth > desired
          ? (desired * othersSpec) / (tableWidth - desired)
          : desired
        // Overflows the table: the columns keep their exact widths.
        const next = othersSpec + scaled > tableWidth ? desired : scaled
        return { ...prev, [state.columnId]: Math.max(80, Math.round(next)) }
      })
    }
    const handleMouseUp = () => {
      resizeRef.current = null
      setIsResizing(false)
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
  }

  const provider = opts.activeFilter?.provider
  const model = opts.activeFilter?.model
  const { activeSource, sessionFilter, toolFilter, deviceFilter } = opts
  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: String(LOGS_PAGE_SIZE) })
    if (provider) params.set('provider', provider)
    if (model) params.set('model', model)
    if (activeSource) params.set('client_source', activeSource)
    if (sessionFilter) params.set('session_id', sessionFilter)
    if (toolFilter) params.set('tool_name', toolFilter)
    if (deviceFilter) params.set('device_id', deviceFilter)
    return params.toString()
  }, [provider, model, activeSource, sessionFilter, toolFilter, deviceFilter])

  const fetchPage = useCallback(async (controller: AbortController, before?: string): Promise<UsageRow[]> => {
    const url = new URL('/usage', window.location.origin)
    url.search = query
    if (before) url.searchParams.set('before', before)
    const response = await fetch(url.toString(), { signal: controller.signal })
    if (!response.ok) throw new Error(t('Failed to fetch log data'))
    return response.json()
  }, [query])

  useEffect(() => {
    requestRef.current?.abort()
    const controller = new AbortController()
    requestRef.current = controller
    filterRef.current = query
    busyRef.current = true
    async function refresh() {
      // Yield before publishing loading state; cleanup can cancel a superseded
      // refresh before any of its state updates become visible.
      await Promise.resolve()
      if (controller.signal.aborted) return
      setUsageRows([])
      setHasMore(true)
      setExpandedRow(null)
      setScrollReset(value => value + 1)
      setLogsLoading(true)
      setLoadMoreFailed(false)
      setInitialLoadFailed(false)
      setError(null)
      try {
        const data = await fetchPage(controller)
        if (controller.signal.aborted) return
        setUsageRows(data)
        setHasMore(data.length === LOGS_PAGE_SIZE)
      } catch (err) {
        if (!controller.signal.aborted) {
          setInitialLoadFailed(true)
          setError(err instanceof Error ? err.message : t('Unknown error'))
        }
      } finally {
        if (!controller.signal.aborted) {
          busyRef.current = false
          setLogsLoading(false)
        }
      }
    }
    void refresh()
    return () => controller.abort()
  }, [query, fetchPage, refreshTrigger, setError])

  const loadMore = useCallback(async () => {
    const last = rowsRef.current.at(-1)
    if (busyRef.current || !hasMore || !last || filterRef.current !== query) return
    const controller = new AbortController()
    requestRef.current = controller
    busyRef.current = true
    setLoadMoreFailed(false)
    setLogsLoading(true)
    setError(null)
    try {
      const data = await fetchPage(controller, cursorFor(last))
      if (controller.signal.aborted) return
      setUsageRows(rows => {
        const ids = new Set(rows.map(row => row.id))
        return [...rows, ...data.filter(row => !ids.has(row.id))]
      })
      setHasMore(data.length === LOGS_PAGE_SIZE)
    } catch (err) {
      if (!controller.signal.aborted) {
        setLoadMoreFailed(true)
        setError(err instanceof Error ? err.message : t('Unknown error'))
      }
    } finally {
      if (!controller.signal.aborted) {
        busyRef.current = false
        setLogsLoading(false)
      }
    }
  }, [fetchPage, hasMore, query, setError])

  // A load-more request may outlive the refresh effect's own controller.
  useEffect(() => () => requestRef.current?.abort(), [])

  return {
    usageRows, setUsageRows, hasMore, loadMore, loadMoreFailed, initialLoadFailed, scrollReset,
    logsLoading, expandedRow, setExpandedRow,
    colWidths, isResizing, handleResizeStart,
  }
}
