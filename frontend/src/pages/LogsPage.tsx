import { Fragment, useState, useEffect, useLayoutEffect, useCallback, useRef, useMemo, type CSSProperties } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useApp } from '../contexts/AppContext'
import { useDashboardData } from '../hooks/useDashboardData'
import { useLogsData } from '../hooks/useLogsData'
import { DeviceSelect } from '../components/DeviceSelect'
import { useRequestLogColumns, type RequestLogColumnId } from '../hooks/useRequestLogColumns'
import { useSessionSelectorData } from '../hooks/useSessionSelectorData'
import { ModelSelector } from '../ModelSelector'
import { RequestLogColumnsControl } from '../components/RequestLogColumnsControl'
import { SessionSelector } from '../components/SessionSelector'
import { ClickToCopy } from '../components/CopyButton'
import { RequestTimeline, type TimelineTool } from '../components/RequestTimeline'
import { t } from '../i18n/index.ts'
import {
  formatCost, formatLatency, formatNumber, formatRate, formatSpeed, formatTime,
  value, getProviderIcon, getProviderBadgeBg, getProviderBadgeText, getModelIcon, getSourceIcon, getSourceBadgeBg, getSourceBadgeText, shortSessionId, resolveTimezone, ToolBadge, formatModelName, formatProviderName,
} from '../utils'
import { DATE_RANGE_OPTIONS, getSinceDate } from '../dateRange.ts'
import { getModelBadgeBackgroundColor, getModelTextColor } from '../model-badge'
import type { DateRangeOption } from '../types'

export function LogsPage() {
  // Read session filter from sessionStorage (set by Dashboard/Sessions tab navigation)
  const storedSessionFilter = useMemo(() => {
    try {
      const raw = sessionStorage.getItem('tokenage-logs-filters')
      if (raw) return JSON.parse(raw).sessionFilter ?? null
    } catch { /* ignore */ }
    return null
  }, [])

  // Clear the one-shot session filter after commit
  useEffect(() => {
    sessionStorage.removeItem('tokenage-logs-filters')
  }, [])

  const { showToast, requestUsageRefresh, activeFilter, setActiveFilter, activeSource, setActiveSource, activeDevice, setActiveDevice, dateRange, setDateRange, timezone, refreshTrigger } = useApp()
  const tz = resolveTimezone(timezone)
  const {
    columns,
    visibleColumns,
    visibleColumnIds: selectedColumnIds,
    setVisibleColumnIds: setSelectedColumnIds,
    resetColumns,
  } = useRequestLogColumns()

  // Dashboard data for summary/sources/providerColors
  const {
    summary, sources, providerColors,
  } = useDashboardData({ includeDaily: false, includeHeatmap: false, includeCount: false })

  // Session filter state (local to logs view)
  const [sessionFilter, setSessionFilter] = useState<string | null>(storedSessionFilter)

  // Tool filter state (local to logs view)
  const [toolFilter, setToolFilter] = useState<string | null>(null)

  // Hovering a row highlights every other row of the same session
  const [hoveredSession, setHoveredSession] = useState<string | null>(null)

  // Logs data hook
  const {
    usageRows, setUsageRows, hasMore, loadMore, loadMoreFailed, scrollReset,
    logsLoading, expandedRow, setExpandedRow,
    colWidths, isResizing, handleResizeStart,
  } = useLogsData({ activeFilter, activeSource, sessionFilter, toolFilter, deviceFilter: activeDevice, dateRange })

  // Sessions data for the session filter dropdown
  const { sessions } = useSessionSelectorData({ activeSource, dateRange })

  // Distinct tool names for the tool filter dropdown (fetched from API, filtered by time range)
  const [availableTools, setAvailableTools] = useState<string[]>([])
  useEffect(() => {
    const controller = new AbortController()
    const since = getSinceDate(dateRange)
    const url = new URL('/usage/tools', window.location.origin)
    if (since) url.searchParams.set('since', since)
    fetch(url.toString(), { signal: controller.signal })
      .then(r => r.ok ? r.json() : [])
      .then(data => { if (!controller.signal.aborted) setAvailableTools(data) })
      .catch((err) => { if (err?.name !== 'AbortError') console.warn('Failed to fetch tools', err) })
    return () => controller.abort()
  }, [dateRange, refreshTrigger])

  const tableContainerRef = useRef<HTMLDivElement>(null)
  const [tableWidth, setTableWidth] = useState(0)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const rowVirtualizer = useVirtualizer({
    count: usageRows.length,
    getScrollElement: () => tableContainerRef.current,
    estimateSize: () => 52,
    getItemKey: useCallback((index: number) => usageRows[index].id, [usageRows]),
    overscan: 8,
    paddingStart: 40,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const anchorRef = useRef<{ id: string; offset: number } | null>(null)
  const previousList = useRef({ head: '', scrollReset: -1 })
  const rememberAnchor = () => {
    const scrollTop = tableContainerRef.current?.scrollTop ?? 0
    const item = rowVirtualizer.getVirtualItemForOffset(scrollTop)
    anchorRef.current = item ? { id: String(item.key), offset: item.start - scrollTop } : null
  }
  useLayoutEffect(() => {
    const head = usageRows[0]?.id ?? ''
    if (previousList.current.scrollReset !== scrollReset) {
      rowVirtualizer.scrollToOffset(0)
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
    const sentinel = sentinelRef.current
    const root = tableContainerRef.current
    if (!sentinel || !root || !hasMore || logsLoading || loadMoreFailed) return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) void loadMore()
    }, { root, rootMargin: '300px' })
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
      if (!visibleIds.includes(col)) continue
      if (toRemove.size >= visibleIds.length - 1) break
      toRemove.add(col)
      remaining -= colWidths[col] ?? 100
      if (remaining <= tableWidth) break
    }

    return visibleColumns.filter(c => !toRemove.has(c.id))
  }, [visibleColumns, colWidths, tableWidth, isResizing])

  const getHeaderStyle = (columnId: RequestLogColumnId): CSSProperties => ({
    width: colWidths[columnId],
    position: 'relative',
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

  const renderRequestCell = (row: (typeof usageRows)[number], columnId: RequestLogColumnId) => {
    switch (columnId) {
      case 'time':
        return <td style={{ color: 'var(--text-secondary)' }}>{formatTime(row.ts, tz)}</td>
      case 'model':
        return (
          <td style={{ padding: '8px' }}>
            <div style={{
              padding: '4px 6px',
              borderRadius: '6px',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              fontSize: '11px',
              backgroundColor: getModelBadgeBackgroundColor(row.model),
              color: getModelTextColor(row.model),
              maxWidth: colWidths.model - 10,
              fontWeight: 600,
              cursor: 'pointer'
            }} title={row.model}>
              {getModelIcon(row.model)}
              <span style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap'
              }}>
                {formatModelName(row.model)}
              </span>
            </div>
          </td>
        )
      case 'provider':
        return (
          <td style={{ padding: '8px' }}>
            <div style={{
              padding: '4px 6px',
              borderRadius: '6px',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              fontSize: '11px',
              backgroundColor: getProviderBadgeBg(row.provider),
              color: getProviderBadgeText(row.provider),
              width: 'fit-content',
              fontWeight: 600
            }}>
              {getProviderIcon(row.provider)}
              {formatProviderName(row.provider)}
            </div>
          </td>
        )
      case 'source':
        return (
          <td style={{ padding: '8px', paddingLeft: '16px' }}>
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              maxWidth: '120px',
            }}>
              {getSourceIcon(row.client_source ?? '') ?? (
                <span style={{
                  fontSize: '11px',
                  fontWeight: 600,
                  padding: '2px 6px',
                  borderRadius: '4px',
                  backgroundColor: getSourceBadgeBg(row.client_source ?? ''),
                  color: getSourceBadgeText(row.client_source ?? ''),
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}>
                  {row.client_source || '—'}
                </span>
              )}
            </div>
          </td>
        )
      case 'device':
        return (
          <td style={{ color: row.device_name ? 'var(--text-secondary)' : 'var(--text-muted)', maxWidth: colWidths.device, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.device_name ?? undefined}>
            {row.device_name ?? t('Unknown')}
          </td>
        )
      case 'session':
        return (
          <td className="request-log-session-cell">
            {row.session_id ? (() => {
              const sessionId = row.session_id
              return (
                <div className="request-log-session-actions">
                  <button
                    type="button"
                    className={`request-log-session-filter${hoveredSession === sessionId ? ' session-hover-active' : ''}`}
                    title={sessionId}
                    aria-label={`${t('Filter logs by session')}: ${sessionId}`}
                    onClick={(e) => { e.stopPropagation(); setSessionFilter(sessionId) }}
                  >
                    {shortSessionId(sessionId)}
                  </button>
                </div>
              )
            })() : (
              <span className="request-log-session-empty">&mdash;</span>
            )}
          </td>
        )
      case 'input':
        return (
          <td style={{ verticalAlign: 'top' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>
                {formatNumber(row.prompt_tokens)}
                <span style={{ fontSize: '10px', fontWeight: 400, marginLeft: '4px', color: 'var(--text-secondary)' }}>{t('tokens')}</span>
                {value(row.prompt_length) > 0 && (
                  <span style={{ fontSize: '10px', fontWeight: 400, marginLeft: '6px', color: 'var(--text-muted)' }}>
                    {t('(Prompt:')} {formatNumber(row.prompt_length)}{t(' chars)')}
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '2px' }}>
                {value(row.cached_tokens) > 0 && (
                  <div style={{ fontSize: '9px', color: 'var(--color-green)', fontWeight: 700 }}>
                    {/* cache_creation_tokens is disjoint from prompt_tokens (Anthropic semantics),
                        so it's added to the denominator for a true hit rate. */}
                    {t('Cache read')} {formatNumber(row.cached_tokens)} ({Math.round((value(row.cached_tokens) / ((value(row.prompt_tokens) + value(row.cache_creation_tokens)) || 1)) * 100)}%)
                  </div>
                )}
                {value(row.cache_creation_tokens) > 0 && (
                  <div style={{ fontSize: '9px', color: 'var(--color-purple)', fontWeight: 700 }}>
                    {t('Cache write')} {formatNumber(row.cache_creation_tokens)}
                  </div>
                )}
              </div>
            </div>
            <div className="has-tooltip" style={{ width: '100%', borderBottom: 'none' }}>
              {(() => {
                const promptUncached = Math.max(0, value(row.prompt_tokens) - value(row.cached_tokens));
                const cacheCreation = value(row.cache_creation_tokens);
                // total_tokens includes cache_creation_tokens for Anthropic responses,
                // so it's rendered as its own segment or the bar won't fill correctly.
                const total = value(row.total_tokens) || 1;
                return (
                  <>
                    <div style={{
                      width: '100%',
                      height: '3px',
                      background: 'var(--progress-bg)',
                      borderRadius: '2px',
                      marginTop: '4px',
                      overflow: 'hidden',
                      border: '1px solid var(--border-color)',
                      display: 'flex'
                    }}>
                      <div style={{ height: '100%', background: 'var(--color-green)', width: `${(value(row.cached_tokens) / total) * 100}%` }} />
                      <div style={{ height: '100%', background: 'var(--color-blue)', width: `${(promptUncached / total) * 100}%`, opacity: 0.7 }} />
                      {cacheCreation > 0 && (
                        <div style={{ height: '100%', background: 'var(--color-indigo)', width: `${(cacheCreation / total) * 100}%` }} />
                      )}
                      <div style={{ height: '100%', background: 'var(--color-purple)', width: `${(value(row.completion_tokens) / total) * 100}%` }} />
                    </div>
                    <div className="tooltip-text">
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
                          <span style={{ color: 'var(--color-green)' }}>&bull; {t('Cached')}:</span>
                          <span style={{ fontWeight: 600 }}>{formatNumber(row.cached_tokens)}</span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
                          <span style={{ color: 'var(--color-blue)' }}>&bull; {t('Input')}:</span>
                          <span style={{ fontWeight: 600 }}>{formatNumber(promptUncached)}</span>
                        </div>
                        {cacheCreation > 0 && (
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
                            <span style={{ color: 'var(--color-indigo)' }}>&bull; {t('Cache write')}:</span>
                            <span style={{ fontWeight: 600 }}>{formatNumber(cacheCreation)}</span>
                          </div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
                          <span style={{ color: 'var(--color-purple)' }}>&bull; {t('Output')}:</span>
                          <span style={{ fontWeight: 600 }}>{formatNumber(row.completion_tokens)}</span>
                        </div>
                      </div>
                    </div>
                  </>
                );
              })()}
            </div>
          </td>
        )
      case 'output':
        return (
          <td style={{ fontWeight: 600, color: 'var(--color-blue)', verticalAlign: 'top' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div>{formatNumber(row.completion_tokens)}</div>
              {value(row.reasoning_tokens) > 0 && (
                <div style={{ fontSize: '9px', color: 'var(--text-secondary)', fontWeight: 700 }}>
                  {t('Reasoning')} {formatNumber(row.reasoning_tokens)} ({Math.min(Math.round((value(row.reasoning_tokens) / (value(row.completion_tokens) || 1)) * 100), 100)}%)
                </div>
              )}
            </div>
            {value(row.reasoning_tokens) > 0 && (
              <div
                style={{
                  width: '100%',
                  height: '3px',
                  background: 'var(--progress-bg)',
                  borderRadius: '2px',
                  marginTop: '4px',
                  overflow: 'hidden',
                  border: '1px solid var(--border-color)',
                  display: 'flex'
                }}
              >
                {value(row.reasoning_tokens) > 0 && (
                  <div
                    title={`Reasoning: ${formatNumber(row.reasoning_tokens)} tokens`}
                    style={{
                      width: `${Math.min((value(row.reasoning_tokens) / (value(row.completion_tokens) || 1)) * 100, 100)}%`,
                      height: '100%',
                      background: '#64748b'
                    }}
                  />
                )}
              </div>
            )}
          </td>
        )
      case 'cost':
        return (
          <td style={{ padding: '8px' }}>
            {(() => {
              const total = value(row.total_cost_usd);
              const isRecalculating = recalculatingId === row.id;
              const costValueClass = isRecalculating ? 'cost-recalculating' : '';
              const handleCostClick = (e: React.MouseEvent) => {
                e.stopPropagation();
                void handleRecalculateCost(row.id);
              };

              const prompt = value(row.prompt_tokens);
              const cached = value(row.cached_tokens);
              const cacheCreation = value(row.cache_creation_tokens);
              const outputCost = value(row.output_cost_usd);

              // The backend splits input cost by component at record time using
              // the selected pricing tier; use those values directly instead of
              // re-deriving them from input_cost_usd by token ratios.
              const actualInputCost = value(row.normal_input_cost_usd);
              const cacheCost = value(row.cache_read_cost_usd);
              const cacheWriteCost = value(row.cache_write_cost_usd);

              const uncachedTokens = Math.max(0, prompt - cached);
              const completionTokens = value(row.completion_tokens);

              // Rates and provenance come from the backend's pricing snapshot so
              // they reflect exactly how this row was priced.
              const pricing = row.pricing;

              return (
                <div className="has-tooltip" style={{ borderBottom: 'none' }}>
                  <button
                    type="button"
                    className={`cost-recalc-btn ${costValueClass}`}
                    style={{ color: 'var(--color-green)', fontWeight: 500 }}
                    onClick={handleCostClick}
                    title={t('Click to recalculate')}
                    aria-label={t('Click to recalculate')}
                  >
                    {formatCost(total)}
                  </button>
                  {row.cost_estimated && (
                    <span
                      title={t('Estimated: priced from current rates, not a stored snapshot')}
                      style={{
                        marginLeft: '4px',
                        fontSize: '10px',
                        color: 'var(--color-yellow, #b58900)',
                      }}
                    >
                      est.
                    </span>
                  )}
                  <div className="tooltip-text" style={{ width: '200px', marginLeft: '-100px' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                        <span style={{ color: 'rgba(255, 255, 255, 0.6)' }}>{t('Input:')}</span>
                        <div style={{ textAlign: 'right' }}>
                          <div>{formatCost(actualInputCost)}</div>
                          {pricing && (
                            <div style={{ fontSize: '9px', color: 'rgba(255, 255, 255, 0.4)' }}>{formatNumber(uncachedTokens)} tokens x {formatRate(pricing.input)}</div>
                          )}
                        </div>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                        <span style={{ color: 'rgba(255, 255, 255, 0.6)' }}>{t('Output:')}</span>
                        <div style={{ textAlign: 'right' }}>
                          <div>{formatCost(outputCost)}</div>
                          {pricing && (
                            <div style={{ fontSize: '9px', color: 'rgba(255, 255, 255, 0.4)' }}>{formatNumber(completionTokens)} tokens x {formatRate(pricing.output)}</div>
                          )}
                        </div>
                      </div>
                      {cacheCost > 0 && (
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                          <span style={{ color: 'rgba(255, 255, 255, 0.6)' }}>{t('Cache:')}</span>
                          <div style={{ textAlign: 'right' }}>
                            <div style={{ color: 'var(--color-green)' }}>{formatCost(cacheCost)}</div>
                            {pricing && (
                              <div style={{ fontSize: '9px', color: 'rgba(255, 255, 255, 0.4)' }}>{formatNumber(cached)} tokens x {formatRate(pricing.cache_read)}</div>
                            )}
                          </div>
                        </div>
                      )}
                      {cacheWriteCost > 0 && (
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                          <span style={{ color: 'rgba(255, 255, 255, 0.6)' }}>{t('Cache write:')}</span>
                          <div style={{ textAlign: 'right' }}>
                            <div style={{ color: 'var(--color-purple)' }}>{formatCost(cacheWriteCost)}</div>
                            {pricing && pricing.cache_write !== null && (
                              <div style={{ fontSize: '9px', color: 'rgba(255, 255, 255, 0.4)' }}>{formatNumber(cacheCreation)} tokens x {formatRate(pricing.cache_write)}</div>
                            )}
                          </div>
                        </div>
                      )}
                      <div style={{ marginTop: '4px', paddingTop: '4px', borderTop: '1px solid #334155', display: 'flex', justifyContent: 'space-between', color: 'white' }}>
                        <span style={{ color: 'rgba(255, 255, 255, 0.6)' }}>{t('Total:')}</span>
                        <span>{formatCost(total)}</span>
                      </div>
                      {pricing && (
                        <div style={{ marginTop: '4px', paddingTop: '4px', borderTop: '1px solid #334155', fontSize: '9px', color: 'rgba(255, 255, 255, 0.5)', lineHeight: 1.5 }}>
                          <div>
                            {t('Source:')} {pricing.source ?? t('unknown')}
                            {pricing.estimated && ` (${t('estimated')})`}
                          </div>
                          {pricing.tier && (
                            <div>
                              {t('Tier:')} {formatNumber(pricing.tier.min_tokens)}
                              {pricing.tier.max_tokens === null ? '+' : `–${formatNumber(pricing.tier.max_tokens)}`} {t('tokens')}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })()}
          </td>
        )
      case 'speed':
        return (
          <td style={{ padding: '8px', fontWeight: 600 }}>
            {(() => {
              const tokens = row.completion_tokens ?? row.total_tokens
              const latency = value(row.latency_ms)
              const speed = formatSpeed(tokens, latency)
              return speed ? (
                <span title={`${formatNumber(tokens)} tokens / ${formatLatency(latency)}`}>
                  <span>{speed.split(' ')[0]}</span>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 400, marginLeft: '3px' }}>{speed.split(' ')[1]}</span>
                </span>
              ) : <span style={{ color: 'var(--text-muted)', fontSize: '12px', fontWeight: 400 }}>—</span>
            })()}
          </td>
        )
      case 'status':
        return (
          <td>
            <span className={`badge ${value(row.status) >= 400 ? 'badge-error' : 'badge-success'}`}>
              {row.status ?? '200'}
            </span>
          </td>
        )
      case 'tool':
        return (
          <td>
            {row.tool_names ? (
              <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap' }}>
                {(row.tool_names as string).split(',').filter(Boolean).map((name: string, i: number) => (
                  <ToolBadge key={i} name={name} />
                ))}
              </div>
            ) : null}
          </td>
        )
      default:
        return <td />
    }
  }

  return (
    <div className="logs-page">
      <div className="filter-bar">
        <div className="filter-group">
          <div className="filter-label">{t('Model')}</div>
          <ModelSelector
            activeFilter={activeFilter}
            summary={summary}
            providerColors={providerColors}
            onChange={(f) => { setActiveFilter(f); }}
          />
          </div>

        <div className="filter-group">
          <div className="filter-label">{t('Source')}</div>
          <select
            className="input-plain"
            value={activeSource || ''}
            onChange={(e) => { setActiveSource(e.target.value || null); }}
          >
            <option value="">{t('All Sources')}</option>
            {sources.map(source => (
              <option key={source} value={source}>{source}</option>
            ))}
          </select>
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

        <div className="filter-group">
          <div className="filter-label">{t('Date Range')}</div>
          <select
            className="input-plain"
            value={dateRange}
            onChange={(e) => setDateRange(e.target.value as DateRangeOption)}
          >
            {DATE_RANGE_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{t(o.label)}</option>
            ))}
          </select>
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center', alignSelf: 'flex-end' }}>
           <RequestLogColumnsControl
            columns={columns}
            selectedColumnIds={selectedColumnIds}
            setSelectedColumnIds={setSelectedColumnIds}
            resetColumns={resetColumns}
           />
           <button
            className="btn-ghost btn-refresh"
            onClick={requestUsageRefresh}
            aria-label={t('Refresh')}
            title={t('Refresh')}
           >
             <span className="refresh-icon">&#x21bb;</span>
           </button>
           <button
            style={{ padding: '8px 16px', background: 'var(--tab-toggle-bg)', color: 'var(--text-secondary)', borderRadius: '8px', fontSize: '12px', fontWeight: 600, border: '1px solid var(--border-color)' }}
            onClick={() => {
              setActiveFilter(null)
              setActiveSource(null)
              setActiveDevice(null)
              setSessionFilter(null)
              setToolFilter(null)
              setDateRange('24h')
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
          <div ref={tableContainerRef} onScroll={rememberAnchor} className="panel-body" style={{ padding: 0, maxHeight: '70vh', overflow: 'auto', overflowAnchor: 'none' }}>
            <table className="table" style={{ tableLayout: 'fixed' }} data-request-log-column-count={effectiveVisibleColumns.length}>
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
                <tr aria-hidden="true"><td colSpan={effectiveVisibleColumns.length} style={{ height: virtualRows[0].start - 40, padding: 0, border: 0 }} /></tr>
              )}
            </tbody>
              {virtualRows.map(virtualRow => {
                const row = usageRows[virtualRow.index]
                return (
                <tbody key={row.id} data-index={virtualRow.index} ref={rowVirtualizer.measureElement}>
                  <tr
                    className={`expandable-row${expandedRow === row.id ? ' expanded' : ''}`}
                    onMouseEnter={() => setHoveredSession(row.session_id ?? null)}
                    onMouseLeave={() => setHoveredSession(null)}
                    onClick={() => setExpandedRow(expandedRow === row.id ? null : row.id)}
                  >
                    {effectiveVisibleColumns.map((column) => (
                      <Fragment key={column.id}>{renderRequestCell(row, column.id)}</Fragment>
                    ))}
                  </tr>
                  {expandedRow === row.id && (
                    <tr className="expanded-row">
                      <td colSpan={effectiveVisibleColumns.length}>
                        <div className="expanded-detail">
                          <div className="detail-group">
                            <span className="detail-label">{t('Request ID')}</span>
                            <span className="detail-value">
                              <ClickToCopy text={row.id} onCopy={showToast}>
                                {row.id}
                              </ClickToCopy>
                            </span>
                          </div>
                          {row.session_id && (
                            <div className="detail-group">
                              <span className="detail-label">{t('Session ID')}</span>
                              <span className="detail-value">
                                <ClickToCopy text={row.session_id} onCopy={showToast}>
                                  {row.session_id}
                                </ClickToCopy>
                              </span>
                            </div>
                          )}
                          <div className="detail-group">
                            <span className="detail-label">{t('Source')}</span>
                            <span className="detail-value" style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                              padding: '2px 8px',
                              borderRadius: '4px',
                              fontSize: '11px',
                              fontWeight: 600,
                              width: 'fit-content',
                              backgroundColor: getSourceBadgeBg(row.client_source ?? ''),
                              color: getSourceBadgeText(row.client_source ?? ''),
                            }}>
                              {getSourceIcon(row.client_source ?? '')}
                              {row.client_source || '—'}
                            </span>
                          </div>
                          <div className="detail-group">
                            <span className="detail-label">{t('Latency')}</span>
                            <span className="detail-value">{formatLatency(row.latency_ms)}</span>
                          </div>
                          <div className="detail-group">
                            <span className="detail-label">{t('Endpoint')}</span>
                            <span className="detail-value">{row.endpoint}</span>
                          </div>
                          <div className="detail-group">
                            <span className="detail-label">{t('Total Tokens')}</span>
                            <span className="detail-value">{formatNumber(row.total_tokens ?? (value(row.prompt_tokens) + value(row.completion_tokens)))}</span>
                          </div>
                          {value(row.tool_tokens) > 0 && (
                            <div className="detail-group">
                              <span className="detail-label">{t('Tool Tokens')}</span>
                              <span className="detail-value">{formatNumber(row.tool_tokens)}</span>
                            </div>
                          )}
                          {value(row.prompt_length) > 0 && (
                            <div className="detail-group">
                              <span className="detail-label">{t('Prompt Length')}</span>
                              <span className="detail-value">{formatNumber(row.prompt_length)} {t('chars')}</span>
                            </div>
                          )}
                          {value(row.ttft_ms) > 0 && (
                            <div className="detail-group">
                              <span className="detail-label">TTFT</span>
                              <span className="detail-value">{formatLatency(row.ttft_ms)}</span>
                            </div>
                          )}
                          <div className="detail-group">
                            <span className="detail-label">{t('Device')}</span>
                            <span className="detail-value">{row.device_name ?? t('Unknown')}</span>
                          </div>
                          {expandedToolCalls && (
                            <RequestTimeline
                              latencyMs={row.latency_ms}
                              ttftMs={row.ttft_ms}
                              toolCalls={expandedToolCalls}
                            />
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </tbody>
                )
              })}
            <tbody>
              {virtualRows.length > 0 && (
                <tr aria-hidden="true"><td colSpan={effectiveVisibleColumns.length} style={{ height: rowVirtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end, padding: 0, border: 0 }} /></tr>
              )}
              {usageRows.length === 0 && !logsLoading && (
                <tr>
                  <td colSpan={effectiveVisibleColumns.length} style={{ textAlign: 'center', padding: '40px', color: 'var(--text-muted)' }}>
                    {t('No requests found for the selected filters.')}
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
