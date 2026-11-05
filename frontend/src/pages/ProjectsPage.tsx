import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useApp } from '../contexts/AppContext'
import { SessionPageHeader } from './SessionsPage'
import { Drawer } from '../components/Drawer'
import { useProjectsData } from '../hooks/useProjectsData'
import { costPerSession, detailSearch, projectAverageLatency, projectSessionsUrl, projectStackMaximum, projectBuckets } from '../projectState'
import { t } from '../i18n'
import { cacheHitRate, distinctColors, formatCost, formatNumber, formatTime, getAgentDisplayName, getSourceIcon, modelColor, PALETTE, resolveTimezone, shortSessionId, sourceColor, toolColor } from '../utils'
import type { DateRangeOption, ProjectDetail, ProjectSummary } from '../types'

function AgentMix({ agents }: { agents: Record<string, number> }) {
  const label = Object.entries(agents).map(([agent, count]) => `${getAgentDisplayName(agent) || t('Unknown')}: ${count}`).join(', ')
  return <span className="project-agent-mix" title={label} role="img" aria-label={label}>{Object.entries(agents).map(([agent, count]) => <i key={agent} style={{ flex: count, background: sourceColor(agent) }} />)}</span>
}
function ProjectBars({ title, values, colorOf, money = false, agents = false }: { title: string; values: Record<string, number>; colorOf: (name: string) => string; money?: boolean; agents?: boolean }) {
  const rows = Object.entries(values).sort((a, b) => b[1] - a[1])
  const max = Math.max(1, ...rows.map(([, value]) => value))
  const colors = distinctColors(rows.map(([key]) => key), colorOf)
  return <section className="project-breakdown"><h3>{t(title)}</h3>{rows.length ? rows.map(([key, value]) => <div className="project-hbar" key={key}>
    <span title={key}>{agents ? getAgentDisplayName(key) || t('Unknown') : key}</span><b>{money ? formatCost(value, 2) : formatNumber(value)}</b>
    <div><i style={{ width: `${value / max * 100}%`, background: colors.get(key) }} /></div>
  </div>) : <p className="project-muted">{t('None')}</p>}</section>
}
type ProjectDrawerData = ProjectSummary & ProjectDetail & { active_buckets: number; bucket_count: number }
function ProjectContents({ data, tz, dateRange }: { data: ProjectDrawerData; tz: string; dateRange: DateRangeOption }) {
  const navigate = useNavigate()
  const tiles = [
    ['Cost', formatCost(data.total_cost_usd, 2)], ['Sessions', formatNumber(data.session_count)],
    ['Cost / session', formatCost(costPerSession(data.total_cost_usd, data.session_count), 2)], ['Requests', formatNumber(data.request_count)],
    ['Cache hit', `${cacheHitRate(data.cached_tokens, data.prompt_tokens, data.cache_creation_tokens).toFixed(1)}%`], ['Failed requests', formatNumber(data.failed_requests)],
    ['Avg latency', `${(projectAverageLatency(data.latency_sum_ms, data.request_count) / 1000).toFixed(1)} s`], ['Active days', `${data.active_buckets} / ${data.bucket_count}`],
  ]
  return <>
    <div className="project-tiles">{tiles.map(([label, value]) => <div key={label}><span>{t(label)}</span><strong>{value}</strong></div>)}</div>
    <section className="project-breakdown"><h3>{t('Recent sessions')}</h3><div className="project-table-scroll"><table className="table project-recent-table"><thead><tr>{['Session', 'Agent', 'Last used', 'Requests', 'Cost'].map(label => <th key={label}>{t(label)}</th>)}</tr></thead>
      <tbody>{data.recent_sessions.map(session => {
        const open = () => navigate(projectSessionsUrl(data.key, dateRange, session.session_id))
        return <tr key={session.session_id} className="project-click-row" role="button" tabIndex={0} onClick={open} onKeyDown={event => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); open() } }}>
          <td title={session.session_id}>{shortSessionId(session.session_id)}</td><td><span className="session-agent">{getSourceIcon(session.client_source)}{getAgentDisplayName(session.client_source)}</span></td><td>{formatTime(session.ended, tz)}</td><td>{formatNumber(session.request_count)}</td><td>{formatCost(session.total_cost_usd, 2)}</td>
        </tr>
      })}</tbody></table></div></section>
    <ProjectBars title="Cost by model" values={data.models} colorOf={modelColor} money />
    <ProjectBars title="Sessions by agent" values={data.agents} colorOf={sourceColor} agents />
    <ProjectBars title="Top tools" values={data.tools} colorOf={toolColor} />
  </>
}
export function ProjectsPage() {
  const { timezone } = useApp()
  const [dateRange, setDateRange] = useState<DateRangeOption>('30d')
  const tz = resolveTimezone(timezone)
  const [search, setSearch] = useSearchParams()
  const project = search.get('project') || null
  const { list, detail } = useProjectsData(project, dateRange)
  const data = list.data
  const buckets = projectBuckets(data?.over_time ?? [], dateRange)
  const names = data?.projects.map(row => row.key) ?? []
  const order = new Map(names.map((name, index) => [name, index]))
  const colors = distinctColors(names, name => PALETTE[order.get(name)! % PALETTE.length])
  const maximum = projectStackMaximum(buckets)
  const row = data?.projects.find(row => row.key === project)
  const selected = row && detail.data ? {
    ...row, ...detail.data,
    active_buckets: buckets.filter(bucket => (bucket.by_project[row.key] ?? 0) > 0).length,
    bucket_count: buckets.length,
  } : null
  const toggle = (name: string) => setSearch(current => detailSearch(current, 'project', project === name ? null : name))
  return <>
    <SessionPageHeader dateRangeControl={{ value: dateRange, onChange: setDateRange }} title="Projects" description="Every repository or folder your agents worked in." />
    <div className="projects-page">
      {list.loading && !data ? <div className="read-state">{t('Loading...')}</div> : list.error && !data ? <div role="alert" className="read-state is-error">{t('Failed to fetch projects')}</div> : data && <>
        <section className="panel session-chart project-chart"><h3>{t('Sessions per day, by project')}</h3>
          <div className="project-stacked-bars">{buckets.map((bucket, index) => {
            const label = `${bucket.period}: ${Object.entries(bucket.by_project).map(([name, count]) => `${name}: ${count}`).join(', ')}`
            return <div key={bucket.key} className="project-stacked-bar" tabIndex={0} title={label} aria-label={label}>
              {Object.entries(bucket.by_project).sort(([a], [b]) => (order.get(a) ?? 0) - (order.get(b) ?? 0)).map(([name, count]) => <i key={name} style={{ height: `${count / maximum * 100}%`, background: colors.get(name) }} />)}
              {index % Math.ceil(buckets.length / 4) === 0 && <small>{new Intl.DateTimeFormat(undefined, { timeZone: tz, month: 'short', day: 'numeric' }).format(new Date(bucket.start))}</small>}
            </div>
          })}</div>
          <div className="project-legend">{data.projects.map(row => <span key={row.key} title={row.key}><i style={{ background: colors.get(row.key) }} />{row.key}</span>)}</div>
        </section>
        <section className="panel"><div className="project-table-scroll"><table className="table projects-table"><thead><tr>{['Project', 'Sessions', 'Cost', 'Cost / session', 'Requests', 'Cache hit', 'Agents', 'Last active'].map(label => <th key={label}>{t(label)}</th>)}</tr></thead><tbody>{data.projects.map(row => <tr key={row.key} role="button" tabIndex={0} aria-pressed={project === row.key} className={`project-click-row${project === row.key ? ' selected' : ''}`} onClick={event => { event.currentTarget.focus(); toggle(row.key) }} onKeyDown={event => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); toggle(row.key) } }}>
          <td><span className="project-name" title={row.key}>{row.key}</span></td><td>{formatNumber(row.session_count)}</td><td>{formatCost(row.total_cost_usd, 2)}</td><td>{formatCost(costPerSession(row.total_cost_usd, row.session_count), 2)}</td><td>{formatNumber(row.request_count)}</td><td>{cacheHitRate(row.cached_tokens, row.prompt_tokens, row.cache_creation_tokens).toFixed(1)}%</td><td><AgentMix agents={row.agents} /></td><td>{formatTime(row.last_active, tz)}</td>
        </tr>)}</tbody></table></div>
          {!data.projects.length && <div className="read-state">{t('No projects yet.')}</div>}
        </section>
      </>}
    </div>
    {project !== null && <Drawer key={project} width={480} title={project} onClose={() => setSearch(current => detailSearch(current, 'project', null))}
      actions={<Link className="btn-ghost" to={projectSessionsUrl(project, dateRange)}>{t('Open in Sessions')} →</Link>}
      meta={selected ? `${t('First session')} ${formatTime(selected.first_started, tz)} · ${t('Last active')} ${formatTime(selected.last_active, tz)} · ${t('Active {active} of {total} days').replace('{active}', String(selected.active_buckets)).replace('{total}', String(selected.bucket_count))}` : undefined}>
      {(detail.loading || list.loading) && !selected ? <div className="read-state">{t('Loading...')}</div> : detail.error ? <div role="alert" className="read-state is-error">{t('Failed to fetch project details')}</div> : selected ? <ProjectContents data={selected} tz={tz} dateRange={dateRange} /> : <div role="alert" className="read-state is-error">{t('Failed to fetch project details')}</div>}
    </Drawer>}
  </>
}
