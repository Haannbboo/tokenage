import { appendModels } from '../modelFilter'
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
  input: 300,
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
  const resizeRef = useRef<{
    columnId: RequestLogColumnId
    startX: number
    /** The column's rendered width when the drag started. */
    startWidth: number
    /** The other columns' rendered widths when the drag started. */
    others: Partial<Record<RequestLogColumnId, number>>
    /** The visible width the columns fill when they are narrower than it. */
    panelWidth: number
  } | null>(null)


  const handleResizeStart = (columnId: RequestLogColumnId, e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const thEl = (e.currentTarget as HTMLElement).closest('th')
    const table = thEl?.closest('table')
    const others: Partial<Record<RequestLogColumnId, number>> = {}
    for (const th of table?.querySelectorAll<HTMLElement>('thead th[data-column-id]') ?? []) {
      const id = th.dataset.columnId as RequestLogColumnId
      if (th !== thEl && th.offsetWidth > 0) others[id] = th.offsetWidth
    }
    resizeRef.current = {
      columnId,
      startX,
      startWidth: thEl?.offsetWidth || colWidths[columnId],
      others,
      panelWidth: table?.parentElement?.clientWidth ?? 0,
    }
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    const handleMouseMove = (e: MouseEvent) => {
      const state = resizeRef.current
      if (!state) return
      const desired = Math.max(80, state.startWidth + (e.clientX - state.startX))
      // The table may be wider than the panel (it scrolls sideways), so the
      // other columns keep the widths they had and the dragged one takes the
      // dragged width. Only when the columns end up narrower than the panel
      // does fixed layout stretch them all to fill it: solve for the width
      // that still renders at the dragged size.
      const othersWidth = Object.values(state.others).reduce((sum, width) => sum + width, 0)
      const fills = othersWidth + desired < state.panelWidth
      const next = fills ? (desired * othersWidth) / (state.panelWidth - desired) : desired
      setColWidths(prev => ({ ...prev, ...state.others, [state.columnId]: Math.max(80, Math.round(next)) }))
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

  const model = opts.activeFilter?.models
  const provider = opts.activeFilter?.provider
  const { activeSource, sessionFilter, toolFilter, deviceFilter } = opts
  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: String(LOGS_PAGE_SIZE) })
    appendModels(params, model)
    if (provider) params.set('provider', provider)
    if (activeSource) params.set('client_source', activeSource)
    if (sessionFilter) params.set('session_id', sessionFilter)
    if (toolFilter) params.set('tool_name', toolFilter)
    if (deviceFilter) params.set('device_id', deviceFilter)
    return params.toString()
  }, [model, provider, activeSource, sessionFilter, toolFilter, deviceFilter])

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
    colWidths, handleResizeStart,
  }
}
