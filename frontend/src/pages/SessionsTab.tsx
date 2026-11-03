import { Fragment, useEffect, useState } from 'react'
import { useApp } from '../contexts/AppContext'
import { useSessionsData, type SessionBucket } from '../hooks/useSessionsData'
import { ClickToCopy } from '../components/CopyButton'
import { SessionDetail } from '../components/SessionDetail'
import { getSinceDate } from '../dateRange'
import { expandableRowProps } from '../expandableRow'
import { t } from '../i18n'
import { formatCost, formatNumber, formatTime, getSourceIcon, resolveTimezone, getAgentDisplayName, sessionAgentName, shortSessionId, ToolBadge, PALETTE } from '../utils'

export function SessionsTab({ onNavigateToLogs }: { onNavigateToLogs: (filters?: { sessionFilter?: string }) => void }) {
  const { activeSource, dateRange, timezone, showToast } = useApp()
  const [bucket, setBucket] = useState<SessionBucket | null>(null)
  useEffect(() => { setBucket(null) }, [dateRange, activeSource])
  const [hoveredAgent, setHoveredAgent] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const { sessions, sessionsLoading, sessionsError, distribution, distributionLoading, distributionError, hasMoreSessions, sessionSortBy, sessionSortOrder, handleSessionSort, loadMoreSessions } = useSessionsData({ activeSource, dateRange, bucket })
  const tz = resolveTimezone(timezone)
  const toggle = (next: SessionBucket) => setBucket(prev => prev?.kind === next.kind && prev.key === next.key ? null : next)
  const header = (label: string, column: string, className?: string) => <th className={className}><button className="session-plain-button" onClick={() => handleSessionSort(column)}>{t(label)} {sessionSortBy === column ? (sessionSortOrder === 'asc' ? '↑' : '↓') : ''}</button></th>
  const ends = sessions.map(s => s.ended)
  const since = getSinceDate(dateRange)
  const rangeStart = since ? new Date(since).getTime() * 1000 : null
  // A session that started before the range counts from its end, so one that ended before every in-range start still lands at its real time.
  const visibleStarts = sessions.map(s => rangeStart != null && s.started < rangeStart ? s.ended : s.started)
  const min = sessions.some(s => rangeStart == null || s.started >= rangeStart) ? Math.min(...visibleStarts) : rangeStart ?? 0
  const max = ends.length ? Math.max(...ends) : min, span = Math.max(1, max - min)
  const agentColor = (source: string | null | undefined) => PALETTE[Math.max(0, (distribution?.by_agent ?? []).findIndex(row => row.key === source)) % PALETTE.length]
  const agentName = (source: string | null | undefined) => source ? getAgentDisplayName(source) === source ? sessionAgentName(source) : getAgentDisplayName(source) : t('Unknown')
  const shortTimeLabel = (row: { period?: string; start?: string; end?: string }) => {
    const date = new Date(row.start ?? '')
    if (Number.isNaN(date.valueOf())) return row.period ?? ''
    const daily = /^\d{4}-\d{2}-\d{2}$/.test(row.period ?? '')
    return new Intl.DateTimeFormat(undefined, { timeZone: tz, ...(daily ? { month: 'short', day: 'numeric' } : { hour: '2-digit', minute: '2-digit', hour12: false }) }).format(date)
  }
  const bars = (title: string, kind: 'time' | 'duration' | 'cost', data: { key: string | number; session_count: number; period?: string; start?: string; end?: string }[]) => {
    const largest = Math.max(1, ...data.map(r => r.session_count))
    return <section className="panel session-chart"><h3>{t(title)}</h3><div className={`session-bars${bucket?.kind === kind ? ' has-selection' : ''}${data.length > 30 ? ' dense' : ''}`}>{data.map((row, index) => { const key = String(row.key); const label = row.period ?? key; const shortLabel = kind === 'time' ? shortTimeLabel(row) : label; return <button key={key} className={`session-bar${bucket?.kind === kind && bucket.key === key ? ' active' : ''}`} title={`${label}: ${row.session_count}`} aria-label={`${label}: ${row.session_count}`} aria-pressed={bucket?.kind === kind && bucket.key === key} onClick={() => toggle({ kind, key, start: row.start, end: row.end })}><span>{data.length <= 30 ? row.session_count || '' : ''}</span><i style={{ height: `${Math.max(2, row.session_count / largest * 60)}px` }} /><small className={kind === 'time' ? `session-time-label${index === 0 ? ' first' : ''}` : undefined}>{kind !== 'time' || index % Math.ceil(data.length / 4) === 0 ? shortLabel : '\u00a0'}</small></button> })}</div></section>
  }
  return <div className="sessions-page">
    {distributionLoading ? <div>{t('Loading...')}</div> : distributionError ? <div role="alert">{t('Failed to fetch session distributions')}</div> : distribution && <div className="session-charts">
      {bars('Active sessions', 'time', distribution.over_time)}
      <section className="panel session-chart"><h3>{t('By agent')}</h3><div className="session-stack">{distribution.by_agent.map((row) => <button key={row.key} disabled={!row.key} title={`${agentName(row.key)}: ${row.session_count}`} aria-label={`${agentName(row.key)}: ${row.session_count}`} aria-pressed={bucket?.kind === 'agent' && bucket.key === row.key} style={{ flex: Math.max(row.session_count, .05), background: agentColor(row.key), opacity: (hoveredAgent || (bucket?.kind === 'agent' ? bucket.key : null)) && (hoveredAgent || bucket?.key) !== row.key ? .25 : 1 }} onMouseEnter={() => setHoveredAgent(row.key)} onMouseLeave={() => setHoveredAgent(null)} onFocus={() => setHoveredAgent(row.key)} onBlur={() => setHoveredAgent(null)} onClick={() => toggle({ kind: 'agent', key: row.key })} />)}</div><div className="session-legend">{distribution.by_agent.map((row) => <div key={row.key}><i style={{ background: agentColor(row.key) }} /><span>{agentName(row.key)}</span><span>{row.session_count} · {Math.round(row.cost_share * 100)}% {t('of cost')}</span></div>)}</div></section>
      {bars('By duration', 'duration', distribution.by_duration)}{bars('By cost', 'cost', distribution.by_cost)}
    </div>}
    {bucket && <div className="session-filter"><button className="btn-ghost" onClick={() => setBucket(null)}>{t('Filter')}: {bucket.kind === 'time' ? distribution?.over_time.find(r => String(r.key) === bucket.key)?.period : bucket.kind === 'agent' ? agentName(bucket.key) : bucket.key} ×</button></div>}
    <div className="panel session-table-wrap"><table className="table sessions-table"><thead><tr><th>{t('Session')}</th><th>{t('Agent')}</th>{header('Started', 'started')}{header('Last used', 'ended')}<th className="session-activity">{t('Activity')}{sessions.length > 0 && <span className="session-axis"><span>{formatTime(min, tz)}</span><span>{formatTime(max, tz)}</span></span>}</th><th>{t('Tools')}</th>{header('Requests', 'request_count')}{header('Cost', 'total_cost_usd')}</tr></thead><tbody>{sessions.map(session => { const tools = Object.entries(session.tool_calls_json ?? {}).sort((a, b) => b[1] - a[1]); const live = Date.now() - session.ended / 1000 < 5 * 60_000; return <Fragment key={session.session_id}><tr {...expandableRowProps(open === session.session_id, () => setOpen(open === session.session_id ? null : session.session_id))}><td title={session.session_id}><div className="session-id"><ClickToCopy text={session.session_id} onCopy={showToast}>{shortSessionId(session.session_id)}</ClickToCopy></div></td><td><span className="session-agent">{getSourceIcon(session.client_source)} {agentName(session.client_source)}</span></td><td>{formatTime(session.started, tz)}</td><td>{formatTime(session.ended, tz)} {live && <span className="session-live">{t('Live')}</span>}</td><td className="session-activity"><div className="session-span"><i style={{ left: `${Math.max(0, Math.min(100, (session.started - min) / span * 100))}%`, width: `${Math.max(0, (Math.min(max, session.ended) - Math.max(min, session.started)) / span * 100)}%`, background: live ? 'var(--color-green)' : 'var(--chart-2)' }} /></div></td><td>{tools.slice(0, 2).map(([name, count]) => <span key={name} onClick={e => e.stopPropagation()}><ToolBadge name={name} count={count} /></span>)} {tools.length > 2 && <span className="session-tools-more has-tooltip" onClick={e => e.stopPropagation()}><button type="button">+{tools.length - 2}</button><span className="tooltip-text">{tools.slice(2).map(([name, count]) => <span key={name} className="session-tool-extra">{name} · {count}</span>)}</span></span>}</td><td>{formatNumber(session.request_count)}</td><td>{formatCost(session.total_cost_usd, 2)}</td></tr>{open === session.session_id && <tr><td colSpan={8}><SessionDetail session={session} tz={tz} onNavigateToLogs={onNavigateToLogs} /></td></tr>}</Fragment> })}</tbody></table>
      {sessionsLoading && <div className="sessions-empty-state">{t('Loading...')}</div>}{sessionsError && <div role="alert" className="sessions-empty-state">{t('Failed to fetch session data')}</div>}{!sessionsLoading && !sessionsError && sessions.length === 0 && <div className="sessions-empty-state">{t('No sessions yet.')}</div>}{hasMoreSessions && <button className="btn-ghost" disabled={sessionsLoading} onClick={loadMoreSessions}>{t('Load more')}</button>}
    </div>
  </div>
}
