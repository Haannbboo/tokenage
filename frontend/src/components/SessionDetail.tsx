import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import type { SessionSummary, UsageRow } from '../types'
import type { TimelineTool } from './RequestTimeline'
import { RequestDetail } from './RequestDetail'
import { SessionLane } from './SessionLane'
import { renderRequestCell } from './RequestCell'
import { useApp } from '../contexts/AppContext'
import { distinctColors, formatCost, formatDuration, formatNumber, formatModelName, getModelIcon, modelColor } from '../utils'
import { t } from '../i18n'
import { expandableRowProps } from '../expandableRow'
import { SessionContextChart, type SessionContext } from './SessionContextChart'
import { EffortMixSection } from '../effort'

export function SessionDetail({ session, tz, onNavigateToLogs }: { session: SessionSummary; tz?: string; onNavigateToLogs: (filters: { sessionFilter: string }) => void }) {
  const { showToast } = useApp()
  const listRef = useRef<HTMLDivElement>(null)
  const previousSessionId = useRef(session.session_id)
  const [context, setContext] = useState<SessionContext | null>(null)
  const [contextError, setContextError] = useState(false)
  const [rows, setRows] = useState<UsageRow[] | null>(null)
  const [rowsError, setRowsError] = useState(false)
  const [hovered, setHovered] = useState<string | null>(null)
  const [recalculatingId, setRecalculatingId] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [calls, setCalls] = useState<TimelineTool[] | null>(null)
  const [callsError, setCallsError] = useState(false)
  useEffect(() => {
    const c = new AbortController()
    if (previousSessionId.current !== session.session_id) {
      setContext(null); setRows(null)
      previousSessionId.current = session.session_id
    }
    setContextError(false); setRowsError(false)
    fetch(`/sessions/${encodeURIComponent(session.session_id)}/context`, { signal: c.signal }).then(r => { if (!r.ok) throw new Error(); return r.json() }).then(setContext).catch(() => { if (!c.signal.aborted) setContextError(true) })
    const query = new URLSearchParams({ session_id: session.session_id, limit: '30' })
    fetch(`/usage?${query}`, { signal: c.signal }).then(r => { if (!r.ok) throw new Error(); return r.json() }).then(setRows).catch(() => { if (!c.signal.aborted) setRowsError(true) })
    return () => c.abort()
  }, [session.session_id, session.request_count, session.ended])
  useEffect(() => {
    setCalls(null); setCallsError(false)
    if (!selected) return
    const c = new AbortController()
    fetch(`/usage/${encodeURIComponent(selected)}/tool-calls`, { signal: c.signal }).then(r => { if (!r.ok) throw new Error(); return r.json() }).then(setCalls).catch(() => { if (!c.signal.aborted) setCallsError(true) })
    return () => c.abort()
  }, [selected])
  const handleRecalculateCost = async (id: string) => {
    if (recalculatingId) return
    setRecalculatingId(id)
    try {
      const response = await fetch(`/usage/${encodeURIComponent(id)}/recalculate-cost`, { method: 'POST' })
      const data = await response.json().catch(() => null)
      if (!response.ok) { showToast(data?.detail ? String(data.detail) : t('Failed to recalculate cost')); return }
      if (data?.skipped) { showToast(t('No current pricing match — cost left unchanged')); return }
      setRows(current => current?.map(row => row.id === id ? { ...row, ...data.new_costs, pricing: data.pricing, price_snapshot_id: data.price_snapshot_id, cost_estimated: data.cost_estimated } : row) ?? null)
      setContext(current => current && { ...current, points: current.points?.map(point => point.id === id ? { ...point, cost_usd: data.new_costs.total_cost_usd } : point) })
      showToast(t('Cost recalculated'))
    } catch { showToast(t('Failed to recalculate cost')) } finally { setRecalculatingId(null) }
  }
  const scrollToRow = (id: string) => {
    const list = listRef.current
    const row = document.getElementById(`session-request-${id}`)
    if (!list || !row) return
    const bounds = list.getBoundingClientRect(), target = row.getBoundingClientRect()
    if (target.top < bounds.top) list.scrollTop += target.top - bounds.top
    else if (target.bottom > bounds.bottom) list.scrollTop += target.bottom - bounds.bottom
  }
  const hoverMark = (id: string | null) => { setHovered(id); if (id) scrollToRow(id) }
  const selectMark = (id: string) => { setSelected(id); scrollToRow(id) }
  // One model colour map for the chart and the timeline, over every model either shows.
  const modelColors = useMemo(() => distinctColors([...new Set([...rows?.map(row => row.model) ?? [], ...context?.points?.map(point => point.model) ?? []])].sort(), modelColor), [rows, context])
  const models = session.models?.length ? session.models : session.model ? [session.model] : []
  const stats = [[t('Duration'), formatDuration(Math.round(session.duration_s))], [t('Requests'), formatNumber(session.request_count)], [t('Tokens'), formatNumber(session.total_tokens)], [t('Peak context'), context ? formatNumber(context.peak_tokens) : '—'], [t('Cost'), formatCost(session.total_cost_usd, 2)], [t('Models'), models.length ? <span className="session-models">{models.map(model => <span key={model} className="entity-badge mono tinted" style={{ '--tint': modelColors.get(model) ?? modelColor(model) } as CSSProperties} title={model}>{getModelIcon(model)}<span>{formatModelName(model)}</span></span>)}</span> : '—']] as const
  return <div className="session-detail"><div className="session-stats">{stats.map(([label, value]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
    {context?.effort_mix?.length ? <EffortMixSection mix={context.effort_mix} /> : null}
    {contextError ? <p role="alert">{t('Failed to fetch context size')}</p> : !context ? <p>{t('Loading...')}</p> : <SessionContextChart data={context} tz={tz} hoveredId={hovered} hoveredTs={rows?.find(row => row.id === hovered)?.ts} linkedRange={rows?.length && rows.length < context.request_count ? { from: Math.min(...rows.map(row => row.ts)), to: Math.max(...rows.map(row => row.ts)) } : null} onHover={hoverMark} colorOf={model => modelColors.get(model) ?? modelColor(model)} />}
    {rowsError ? <p role="alert">{t('Failed to fetch interactions')}</p> : !rows ? <p>{t('Loading...')}</p> : rows.length === 0 ? <p>{t('No interactions found.')}</p> : <>
      <SessionLane rows={rows} colors={modelColors} selected={selected} hovered={hovered} onHover={hoverMark} onSelect={selectMark} tz={tz} />
      <div className="session-interactions" ref={listRef}><table className="table"><thead><tr>{['Time', 'Model', 'Tokens', 'Cost', 'Latency', 'Status', 'Tools'].map(label => <th key={label}>{t(label)}</th>)}</tr></thead><tbody>{rows.map(row => <Fragment key={row.id}><tr id={`session-request-${row.id}`} {...expandableRowProps(selected === row.id, () => setSelected(selected === row.id ? null : row.id), hovered === row.id ? 'session-row-hovered' : '')} onMouseEnter={() => setHovered(row.id)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(row.id)} onBlur={() => setHovered(null)}>
        {(['time', 'model', 'tokens', 'cost', 'latency', 'status', 'tool'] as const).map(column => <Fragment key={column}>{renderRequestCell(row, column, { tz, colWidths: { model: 200, device: 120 }, hoveredSession: null, setSessionFilter: () => {}, recalculatingId, handleRecalculateCost, stopToolPropagation: true })}</Fragment>)}
      </tr>{selected === row.id && <tr className="expanded-row"><td colSpan={7}>{callsError && <p role="alert">{t('Failed to fetch tool calls')}</p>}{!calls && !callsError ? <p>{t('Loading...')}</p> : <><div className="session-request-breakdown">{[[t('Input'), formatNumber(row.prompt_tokens)], [t('Output'), formatNumber(row.completion_tokens)], [t('Cached'), formatNumber(row.cached_tokens)], [t('Cache write'), formatNumber(row.cache_creation_tokens)], [t('Input cost'), formatCost(row.normal_input_cost_usd)], [t('Output cost'), formatCost(row.output_cost_usd)], [t('Cache cost'), formatCost(row.cache_read_cost_usd)], [t('Cache write cost'), formatCost(row.cache_write_cost_usd)]].map(([label, val]) => <span key={label}>{label}: {val}</span>)}</div><RequestDetail row={row} toolCalls={calls} showToast={showToast} /></>}</td></tr>}</Fragment>)}</tbody></table></div>
    </>}
    <div className="session-pager"><span>{rows?.length ? t('Latest {shown} requests').replace('{shown}', String(rows.length)) : ''}</span><button className="btn-ghost" onClick={() => onNavigateToLogs({ sessionFilter: session.session_id })}>{t('View all in Request Logs')} →</button></div>
  </div>
}
