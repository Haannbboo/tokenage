import { Fragment, useState, useEffect, useLayoutEffect, useCallback, useRef, useMemo, type CSSProperties } from 'react'
import { useWindowVirtualizer } from '@tanstack/react-virtual'
import { useSearchParams } from 'react-router-dom'
import { useApp } from '../contexts/AppContext'
import { useDashboardData } from '../hooks/useDashboardData'
import { useLogsData } from '../hooks/useLogsData'
import { DeviceSelect } from '../components/DeviceSelect'
import { useRequestLogColumns, type RequestLogColumnId } from '../hooks/useRequestLogColumns'
import { useSessionSelectorData } from '../hooks/useSessionSelectorData'
import { ModelSelector } from '../ModelSelector'
import { RequestLogColumnsControl } from '../components/RequestLogColumnsControl'
import { SessionSelector } from '../components/SessionSelector'
import { type TimelineTool } from '../components/RequestTimeline'
import { RequestDetail } from '../components/RequestDetail'
import { t } from '../i18n/index.ts'
import { shortSessionId, resolveTimezone } from '../utils'
import { renderRequestCell } from '../components/RequestCell'
import { PageHeader, RefreshButton, SourceFilter } from '../components/PageHeader'
import { expandableRowProps } from '../expandableRow'

export function LogsPage() {
  // ?session=<id> deep-links the session filter; it stays in the URL so reload and Back keep it.
  const [searchParams, setSearchParams] = useSearchParams()
  const { showToast, requestUsageRefresh, activeFilter, setActiveFilter, activeSource, setActiveSource, activeDevice, setActiveDevice, timezone, refreshTrigger } = useApp()
  const tz = resolveTimezone(timezone)
  const {
    columns,
    visibleColumns,
    visibleColumnIds: selectedColumnIds,
    pinnedColumnIds,
    setColumnVisible,
    resetColumns,
    showAllColumns,
  } = useRequestLogColumns()

  // Filter options cover the full log history, independent of the dashboard range.
  const {
    selectorSummary, sources, providerColors,
  } = useDashboardData({ dateRange: 'all', includeDaily: false, includeHeatmap: false, includeCount: false })

  // Session filter state (local to logs view)
  const sessionFilter = searchParams.get('session')
  const setSessionFilter = (id: string | null) => {
    const next = new URLSearchParams(searchParams)
    if (id) next.set('session', id)
    else next.delete('session')
    setSearchParams(next, { replace: true })
  }

  // Tool filter state (local to logs view)
  const [toolFilter, setToolFilter] = useState<string | null>(null)

  // Hovering a row highlights every other row of the same session
  const [hoveredSession, setHoveredSession] = useState<string | null>(null)

  // Logs data hook
  const {
    usageRows, setUsageRows, hasMore, loadMore, loadMoreFailed, initialLoadFailed, scrollReset,
    logsLoading, expandedRow, setExpandedRow,
    colWidths, isResizing, handleResizeStart,
  } = useLogsData({ activeFilter, activeSource, sessionFilter, toolFilter, deviceFilter: activeDevice })

  // Sessions data for the session filter dropdown
  const { sessions } = useSessionSelectorData({ activeSource, dateRange: 'all' })

  // Distinct tool names across the full log history.
  const [availableTools, setAvailableTools] = useState<string[]>([])
  useEffect(() => {
    const controller = new AbortController()
    const url = new URL('/usage/tools', window.location.origin)
    fetch(url.toString(), { signal: controller.signal })
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (!controller.signal.aborted) setAvailableTools(data) })
      .catch((err) => { if (err?.name !== 'AbortError') console.warn('Failed to fetch tools', err) })
    return () => controller.abort()
  }, [refreshTrigger])

  const tableContainerRef = useRef<HTMLDivElement>(null)
  const tableRef = useRef<HTMLTableElement>(null)
  const filterBarRef = useRef<HTMLDivElement>(null)
  const [tableWidth, setTableWidth] = useState(0)
  // Document offset of the table: the window scrolls, so the virtualizer
  // needs to know where the list starts inside the document.
  const [tableTop, setTableTop] = useState(0)
  const [tableHeaderHeight, setTableHeaderHeight] = useState(0)
  const [filterHeight, setFilterHeight] = useState(0)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useWindowVirtualizer({
    count: usageRows.length,
    estimateSize: () => 52,
    getItemKey: useCallback((index: number) => usageRows[index].id, [usageRows]),
    overscan: 8,
    paddingStart: tableHeaderHeight,
    scrollMargin: tableTop,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const anchorRef = useRef<{ id: string; offset: number } | null>(null)
  const previousList = useRef({ head: '', scrollReset: -1 })
  const rememberAnchor = useCallback(() => {
    const scrollY = window.scrollY
    const item = rowVirtualizer.getVirtualItemForOffset(scrollY)
    // Before the list (including its header), the virtualizer still returns row 0.
    // Preserve the page position there so a prepend leaves new requests in view.
    anchorRef.current = item && scrollY >= item.start ? { id: String(item.key), offset: item.start - scrollY } : null
  }, [rowVirtualizer])
  // Scroll the table back under the sticky filter bar after a filter change.
  const scrollTableIntoView = useCallback(() => {
    const table = tableRef.current
    if (!table) return
    const styles = getComputedStyle(document.documentElement)
    const size = (name: string) => parseFloat(styles.getPropertyValue(name)) || 0
    const navBottom = size('--navbar-top') + size('--navbar-height')
    const top = table.getBoundingClientRect().top + window.scrollY
    window.scrollTo({ top: Math.max(0, top - navBottom - filterHeight - size('--sticky-gap')) })
  }, [filterHeight])
  useLayoutEffect(() => {
    const table = tableRef.current
    if (!table) return
    const top = table.getBoundingClientRect().top + window.scrollY
    setTableTop(prev => (Math.abs(prev - top) < 1 ? prev : top))
    setTableHeaderHeight(table.tHead?.offsetHeight ?? 0)
  })
  useLayoutEffect(() => {
    const head = usageRows[0]?.id ?? ''
    if (previousList.current.scrollReset !== scrollReset) {
      scrollTableIntoView()
    } else if (previousList.current.head !== head && anchorRef.current) {
      const anchor = anchorRef.current
      const index = usageRows.findIndex(row => row.id === anchor.id)
      const offset = index < 0 ? undefined : rowVirtualizer.getOffsetForIndex(index, 'start')
      if (offset) rowVirtualizer.scrollToOffset(offset[0] - anchor.offset)
    }
    previousList.current = { head, scrollReset }
    rememberAnchor()
  })
  useEffect(() => {
    const filter = filterBarRef.current
    if (!filter) return
    const update = () => setFilterHeight(prev => (prev === filter.offsetHeight ? prev : filter.offsetHeight))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(filter)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    window.addEventListener('scroll', rememberAnchor, { passive: true })
    return () => window.removeEventListener('scroll', rememberAnchor)
  }, [rememberAnchor])
  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel || !hasMore || logsLoading || loadMoreFailed) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) void loadMore()
    }, { rootMargin: '300px' })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasMore, logsLoading, loadMore, loadMoreFailed])

  // Tool calls for expanded row
  const [expandedToolCalls, setExpandedToolCalls] = useState<TimelineTool[] | null>(null)
  useEffect(() => {
    if (!expandedRow) {
      setExpandedToolCalls(null)
      return
    }
    let cancelled = false
    fetch(`/usage/${expandedRow}/tool-calls`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        if (!cancelled) setExpandedToolCalls(data)
      })
      .catch(() => {
        if (!cancelled) setExpandedToolCalls(null)
      })
    return () => {
      cancelled = true
    }
  }, [expandedRow])

  // Per-row cost recalculation
  const [recalculatingId, setRecalculatingId] = useState<string | null>(null)
  const handleRecalculateCost = async (usageId: string) => {
    if (recalculatingId !== null) return
    setRecalculatingId(usageId)
    try {
      const response = await fetch(`/usage/${usageId}/recalculate-cost`, { method: 'POST' })
      const data = await response.json().catch(() => null)
      if (!response.ok) {
        showToast(data?.detail ? String(data.detail) : t('Failed to recalculate cost'))
        return
      }
      if (data?.skipped) {
        showToast(t('No current pricing match — cost left unchanged'))
        return
      }
      showToast(t('Cost recalculated'))
      // Patch just this row's cost fields in place — a full requestUsageRefresh()
      // would refetch and re-render the whole table for a single-cell change.
      setUsageRows(rows => rows.map(row => row.id === usageId ? { ...row, ...data.new_costs, pricing: data.pricing, price_snapshot_id: data.price_snapshot_id, cost_estimated: data.cost_estimated } : row))
    } catch {
      showToast(t('Failed to recalculate cost'))
    } finally {
      setRecalculatingId(null)
    }
  }

  useEffect(() => {
    const container = tableContainerRef.current
    if (!container) return
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setTableWidth(entry.contentRect.width)
      }
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  const effectiveVisibleColumns = useMemo(() => {
    if (tableWidth === 0) return visibleColumns
    // Dropping a column mid-drag moves the handle away from the cursor, so
    // keep the set stable while a column is being resized.
    if (isResizing) return visibleColumns

    const hidePriority: RequestLogColumnId[] = [
      'speed', 'session', 'device', 'source', 'cost', 'status',
      'provider', 'output', 'time', 'input', 'model',
    ]

    const visibleIds = visibleColumns.map(c => c.id)
    const totalWidth = visibleIds.reduce((sum, id) => sum + (colWidths[id] ?? 100), 0)
    if (totalWidth <= tableWidth) return visibleColumns

    const toRemove = new Set<RequestLogColumnId>()
    let remaining = totalWidth
    for (const col of hidePriority) {
      if (!visibleIds.includes(col) || pinnedColumnIds.includes(col)) continue
      if (toRemove.size >= visibleIds.length - 1) break
      toRemove.add(col)
      remaining -= colWidths[col] ?? 100
      if (remaining <= tableWidth) break
    }

    return visibleColumns.filter(c => !toRemove.has(c.id))
  }, [visibleColumns, pinnedColumnIds, colWidths, tableWidth, isResizing])

  const getHeaderStyle = (columnId: RequestLogColumnId): CSSProperties => ({
    width: colWidths[columnId],
  })

  const renderLoadingCell = (columnId: RequestLogColumnId) => {
    switch (columnId) {
      case 'time':
        return <td><div className="skeleton" style={{ width: 90, height: 14 }} /></td>
      case 'model':
        return <td><div className="skeleton" style={{ width: 120, height: 24, borderRadius: 6 }} /></td>
      case 'provider':
        return <td><div className="skeleton" style={{ width: 70, height: 20, borderRadius: 4 }} /></td>
      case 'source':
        return <td><div className="skeleton" style={{ width: 60, height: 20, borderRadius: 4 }} /></td>
      case 'device':
        return <td><div className="skeleton" style={{ width: 70, height: 14 }} /></td>
      case 'session':
        return <td><div className="skeleton" style={{ width: 80, height: 20, borderRadius: 999 }} /></td>
      case 'input':
        return <td><div className="skeleton" style={{ width: 80, height: 14 }} /></td>
      case 'output':
        return <td><div className="skeleton" style={{ width: 60, height: 14 }} /></td>
      case 'cost':
        return <td><div className="skeleton" style={{ width: 50, height: 14 }} /></td>
      case 'speed':
        return <td><div className="skeleton" style={{ width: 80, height: 14 }} /></td>
      case 'status':
        return <td><div className="skeleton" style={{ width: 40, height: 20, borderRadius: 6 }} /></td>
      case 'tool':
        return <td><div className="skeleton" style={{ width: 60, height: 18, borderRadius: 4 }} /></td>
      default:
        return <td />
    }
  }


  return (
    <div className="logs-page" style={{ '--logs-filter-height': `${filterHeight}px` } as CSSProperties}>
      <PageHeader title="Request Logs" description="Individual model requests, newest first." />
      <div className="filter-bar" ref={filterBarRef}>
        <div className="filter-group">
          <div className="filter-label">{t('Model')}</div>
          <ModelSelector
            activeFilter={activeFilter}
            summary={selectorSummary}
            providerColors={providerColors}
            onChange={(f) => { setActiveFilter(f); }}
          />
          </div>

        <div className="filter-group">
          <div className="filter-label">{t('Source')}</div>
          <SourceFilter value={activeSource} sources={sources} onChange={setActiveSource} />
        </div>

        <div className="filter-group">
          <div className="filter-label">{t('Session')}</div>
          <SessionSelector
            sessions={sessions}
            sessionFilter={sessionFilter}
            onChange={(id) => { setSessionFilter(id); }}
            sourceColors={providerColors}
          />
        </div>

        <div className="filter-group">
          <div className="filter-label">{t('Device')}</div>
          <DeviceSelect value={activeDevice} onChange={(id) => { setActiveDevice(id); }} />
        </div>

        <div className="filter-group">
          <div className="filter-label">{t('Tool')}</div>
          <select
            className="input-plain"
            value={toolFilter || ''}
            onChange={(e) => { setToolFilter(e.target.value || null); }}
          >
            <option value="">{t('All Tools')}</option>
            {availableTools.map(name => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center', alignSelf: 'flex-end' }}>
           <RequestLogColumnsControl
            columns={columns}
            selectedColumnIds={selectedColumnIds}
            displayedColumnIds={effectiveVisibleColumns.map(column => column.id)}
            setColumnVisible={setColumnVisible}
            resetColumns={resetColumns}
            showAllColumns={showAllColumns}
           />
           <RefreshButton onClick={requestUsageRefresh} />
           <button
            type="button"
            className="btn-ghost"
            onClick={() => {
              setActiveFilter(null)
              setActiveSource(null)
              setActiveDevice(null)
              setSessionFilter(null)
              setToolFilter(null)
            }}
           >
             {t('Reset')}
           </button>
        </div>
      </div>

      {sessionFilter && (
        <div style={{ marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span className="badge request-log-session-filter-badge" title={sessionFilter}>
            {t('Session')}: {shortSessionId(sessionFilter)}
            <button
              className="btn-ghost"
              aria-label={t('Clear session filter')}
              onClick={() => { setSessionFilter(null) }}
              style={{ padding: '2px 6px', fontSize: '11px' }}
            >
              &times;
            </button>
          </span>
        </div>
      )}

      <div className="panel">
          <div ref={tableContainerRef} className="panel-body" style={{ padding: 0, overflowAnchor: 'none' }}>
            <table
              ref={tableRef}
              className="table"
              style={{ tableLayout: 'fixed', borderCollapse: 'separate', borderSpacing: 0 }}
              data-request-log-column-count={effectiveVisibleColumns.length}
            >
            <thead>
              <tr>
                {effectiveVisibleColumns.map((column) => (
                  <th key={column.id} style={getHeaderStyle(column.id)}>
                    {t(column.label)}
                    <div
                      className="col-resize-handle"
                      onMouseDown={(e) => handleResizeStart(column.id, e)}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {logsLoading && usageRows.length === 0 ? (
                Array.from({ length: 5 }, (_, i) => (
                  <tr key={`skeleton-${i}`}>
                    {effectiveVisibleColumns.map((column) => (
                      <Fragment key={column.id}>{renderLoadingCell(column.id)}</Fragment>
                    ))}
                  </tr>
                ))
              ) : null}
              {virtualRows.length > 0 && (
                <tr aria-hidden="true"><td colSpan={effectiveVisibleColumns.length} style={{ height: Math.max(0, virtualRows[0].start - tableTop - tableHeaderHeight), padding: 0, border: 0 }} /></tr>
              )}
            </tbody>
              {virtualRows.map(virtualRow => {
                const row = usageRows[virtualRow.index]
                return (
                <tbody key={row.id} data-index={virtualRow.index} ref={rowVirtualizer.measureElement}>
                  <tr
                    {...expandableRowProps(expandedRow === row.id, () => setExpandedRow(expandedRow === row.id ? null : row.id))}
                    onMouseEnter={() => setHoveredSession(row.session_id ?? null)}
                    onMouseLeave={() => setHoveredSession(null)}
                  >
                    {effectiveVisibleColumns.map((column) => (
                      <Fragment key={column.id}>{renderRequestCell(row, column.id, { tz, colWidths, hoveredSession, setSessionFilter, recalculatingId, handleRecalculateCost })}</Fragment>
                    ))}
                  </tr>
                  {expandedRow === row.id && (
                    <tr className="expanded-row">
                      <td colSpan={effectiveVisibleColumns.length}>
                        <RequestDetail row={row} toolCalls={expandedToolCalls} showToast={showToast} />
                      </td>
                    </tr>
                  )}
                </tbody>
                )
              })}
            <tbody>
              {/* Item offsets include scrollMargin; total size is table-relative. */}
              {virtualRows.length > 0 && (
                <tr aria-hidden="true"><td colSpan={effectiveVisibleColumns.length} style={{ height: Math.max(0, rowVirtualizer.getTotalSize() - (virtualRows[virtualRows.length - 1].end - tableTop)), padding: 0, border: 0 }} /></tr>
              )}
              {usageRows.length === 0 && !logsLoading && (
                <tr>
                  <td colSpan={effectiveVisibleColumns.length} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>
                    {initialLoadFailed ? (
                      <>{t('Failed to fetch log data')} <button className="btn-ghost" onClick={requestUsageRefresh}>{t('Retry')}</button></>
                    ) : t('No requests found for the selected filters.')}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <div ref={sentinelRef} data-testid="logs-sentinel" role="status" style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)' }}>
            {logsLoading ? t('Loading...') : loadMoreFailed ? <button className="btn-ghost" onClick={() => void loadMore()}>{t('Retry')}</button> : !hasMore ? t('No more logs') : null}
          </div>
        </div>

      </div>
    </div>
  )
}
