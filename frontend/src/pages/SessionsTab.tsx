import { useApp } from '../contexts/AppContext'
import { useSessionsData } from '../hooks/useSessionsData'
import { ClickToCopy } from '../components/CopyButton'
import { t } from '../i18n/index.ts'
import { formatCost, formatNumber, formatTime, getSourceIcon, resolveTimezone, sessionAgentName, shortSessionId, ToolBadge } from '../utils'

export function SessionsTab({ onNavigateToLogs }: {
  onNavigateToLogs: (filters?: { sessionFilter?: string }) => void
}) {
  const { activeSource, dateRange, timezone, showToast } = useApp()
  const { sessions, sessionsLoading, hasMoreSessions, sessionSortBy, sessionSortOrder, handleSessionSort, setSessionPage } = useSessionsData({ activeSource, dateRange })
  const tz = resolveTimezone(timezone)
  const header = (label: string, column: string) => (
    <th onClick={() => handleSessionSort(column)} style={{ cursor: 'pointer' }}>
      {t(label)} {sessionSortBy === column ? (sessionSortOrder === 'asc' ? '↑' : '↓') : ''}
    </th>
  )
  return (
    <div className="sessions-page">
      <div className="panel" style={{ overflowX: 'auto' }}>
        <table className="table sessions-table">
          <thead><tr>
            {header('Session', 'session_id')}<th>{t('Agent')}</th>
            {header('Started', 'started')}{header('Ended', 'ended')}
            {header('Requests', 'request_count')}{header('Cost', 'total_cost_usd')}<th>{t('Tools')}</th>
          </tr></thead>
          <tbody>{sessions.map(session => (
            <tr key={session.session_id}>
              <td title={session.session_id}>
                <ClickToCopy text={session.session_id} onCopy={showToast}><span>{shortSessionId(session.session_id)}</span></ClickToCopy>
                {' '}<button className="btn-ghost" onClick={() => onNavigateToLogs({ sessionFilter: session.session_id })}>{t('View in Logs')}</button>
              </td>
              <td>{getSourceIcon(session.client_source)} {sessionAgentName(session.client_source)}</td>
              <td>{formatTime(session.started, tz)}</td><td>{formatTime(session.ended, tz)}</td>
              <td>{formatNumber(session.request_count)}</td><td>{formatCost(session.total_cost_usd, 2)}</td>
              <td>{session.tool_calls_json ? Object.entries(session.tool_calls_json).map(([name, count]) => <ToolBadge key={name} name={name} count={count} />) : '—'}</td>
            </tr>
          ))}</tbody>
        </table>
        {!sessionsLoading && sessions.length === 0 && <div className="sessions-empty-state">{t('No sessions yet.')}</div>}
        {hasMoreSessions && sessions.length > 0 && <button className="btn-ghost" disabled={sessionsLoading} onClick={() => setSessionPage(page => page + 1)}>{t('Load more')}</button>}
      </div>
    </div>
  )
}
