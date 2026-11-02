import { Fragment, useEffect, useRef, useState } from 'react'
import type { SessionSummary, UsageRow } from '../types'
import type { TimelineTool } from './RequestTimeline'
import { RequestDetail } from './RequestDetail'
import { SessionLane } from './SessionLane'
import { renderRequestCell } from './RequestCell'
import { useApp } from '../contexts/AppContext'
import { formatCost, formatDuration, formatLatency, formatNumber, formatTime } from '../utils'
import { t } from '../i18n'

type Tool = TimelineTool & { usage_id: string | null }
export function SessionDetail({ session, tz, onNavigateToLogs }: { session: SessionSummary; tz?: string; onNavigateToLogs: (filters: { sessionFilter: string }) => void }) {
  const { showToast } = useApp()
  const listRef = useRef<HTMLDivElement>(null)
  const [rows, setRows] = useState<UsageRow[] | null>(null)
  const [tools, setTools] = useState<Tool[] | null>(null)
  const [rowsError, setRowsError] = useState(false)
  const [toolsError, setToolsError] = useState(false)
  const [hovered, setHovered] = useState<string | null>(null)
  const [recalculatingId, setRecalculatingId] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [calls, setCalls] = useState<TimelineTool[] | null>(null)
  const [callsError, setCallsError] = useState(false)
  useEffect(() => {
    const c = new AbortController()
    const query = new URLSearchParams({ session_id: session.session_id, limit: '30' })
    fetch(`/usage?${query}`, { signal: c.signal }).then(r => { if (!r.ok) throw new Error(); return r.json() }).then(setRows).catch(() => { if (!c.signal.aborted) setRowsError(true) })
    fetch(`/sessions/${encodeURIComponent(session.session_id)}/tool-calls`, { signal: c.signal }).then(r => { if (!r.ok) throw new Error(); return r.json() }).then(setTools).catch(() => { if (!c.signal.aborted) setToolsError(true) })
    return () => c.abort()
  }, [session.session_id])
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
  const stats = [[t('Duration'), formatDuration(Math.round(session.duration_s))], [t('Requests'), formatNumber(session.request_count)], [t('Tokens'), formatNumber(session.total_tokens)], [t('Cost'), formatCost(session.total_cost_usd, 2)], [t('Models'), session.models?.length ? session.models.join(', ') : session.model || '—']]
  return <div className="session-detail"><div className="session-stats">{stats.map(([label, value]) => <div key={label}><strong>{value}</strong><span>{label}</span></div>)}</div>
    {rowsError ? <p role="alert">{t('Failed to fetch interactions')}</p> : !rows ? <p>{t('Loading...')}</p> : rows.length === 0 ? <p>{t('No interactions found.')}</p> : <>
      <SessionLane rows={rows} selected={selected} hovered={hovered} onHover={hoverMark} onSelect={selectMark} tz={tz} />
      <div className="session-interactions" ref={listRef}><table className="table"><thead><tr>{['Time', 'Model', 'Tokens', 'Cost', 'Latency', 'Status', 'Tools'].map(label => <th key={label}>{t(label)}</th>)}</tr></thead><tbody>{rows.map(row => <Fragment key={row.id}><tr id={`session-request-${row.id}`} className={`expandable-row${hovered === row.id ? ' session-row-hovered' : ''}`} tabIndex={0} aria-expanded={selected === row.id} onMouseEnter={() => setHovered(row.id)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(row.id)} onBlur={() => setHovered(null)} onClick={() => setSelected(selected === row.id ? null : row.id)} onKeyDown={e => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setSelected(selected === row.id ? null : row.id) } }}>
        {(['time', 'model', 'tokens', 'cost', 'latency', 'status', 'tool'] as const).map(column => <Fragment key={column}>{renderRequestCell(row, column, { tz, colWidths: { model: 200, device: 120 }, hoveredSession: null, setSessionFilter: () => {}, recalculatingId, handleRecalculateCost, stopToolPropagation: true })}</Fragment>)}
      </tr>{selected === row.id && <tr className="expanded-row"><td colSpan={7}>{callsError ? <p role="alert">{t('Failed to fetch tool calls')}</p> : !calls ? <p>{t('Loading...')}</p> : <><div className="session-request-breakdown">{[[t('Input'), formatNumber(row.prompt_tokens)], [t('Output'), formatNumber(row.completion_tokens)], [t('Cached'), formatNumber(row.cached_tokens)], [t('Cache write'), formatNumber(row.cache_creation_tokens)], [t('Input cost'), formatCost(row.normal_input_cost_usd)], [t('Output cost'), formatCost(row.output_cost_usd)], [t('Cache cost'), formatCost(row.cache_read_cost_usd)], [t('Cache write cost'), formatCost(row.cache_write_cost_usd)]].map(([label, val]) => <span key={label}>{label}: {val}</span>)}</div><RequestDetail row={row} toolCalls={calls} showToast={showToast} /></>}</td></tr>}</Fragment>)}</tbody></table></div>
      <div className="session-pager"><span>{t('Latest {shown} requests').replace('{shown}', String(rows.length))}</span><button className="btn-ghost" onClick={() => onNavigateToLogs({ sessionFilter: session.session_id })}>{t('View all in Request Logs')} →</button></div>
    </>}
    {toolsError ? <p role="alert">{t('Failed to fetch tool calls')}</p> : tools && tools.some(tool => !tool.usage_id) && <div><strong>{t('Unattributed')}</strong><ul>{tools.filter(tool => !tool.usage_id).map((tool, i) => <li key={`${tool.tool_use_id}-${i}`}>{tool.tool_name} · {tool.ts ? formatTime(tool.ts, tz) : '—'} · {tool.duration_ms == null ? '—' : formatLatency(tool.duration_ms)}</li>)}</ul></div>}
  </div>
}
