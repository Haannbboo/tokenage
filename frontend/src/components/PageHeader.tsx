import type { ReactNode } from 'react'
import { t } from '../i18n/index.ts'
import { DATE_RANGE_OPTIONS } from '../dateRange.ts'
import type { DateRangeOption } from '../types'
import { Icon } from './Icon'

/** Breadcrumb, title and description on the left; the page's own filters on the right. */
export function PageHeader({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="page-header-text">
        <div className="crumb">tokenage › <b>{t(title)}</b></div>
        <h1>{t(title)}</h1>
        <p>{t(description)}</p>
      </div>
      {children && <div className="page-filters">{children}</div>}
    </header>
  )
}

export function DateRangeFilter({ value, onChange }: { value: DateRangeOption; onChange: (value: DateRangeOption) => void }) {
  return (
    <select id="dashboard-date-range" name="dateRange" aria-label={t('Date Range')} className="input-plain" value={value} onChange={e => onChange(e.target.value as DateRangeOption)}>
      {DATE_RANGE_OPTIONS.map(o => <option key={o.value} value={o.value}>{t(o.label)}</option>)}
    </select>
  )
}

export function SourceFilter({ value, sources, onChange }: { value: string | null; sources: string[]; onChange: (value: string | null) => void }) {
  return (
    <select id="dashboard-source-filter" name="sourceFilter" aria-label={t('Source Filter')} className="input-plain" value={value || ''} onChange={e => onChange(e.target.value || null)}>
      <option value="">{t('All Sources')}</option>
      {sources.map(source => <option key={source} value={source}>{source}</option>)}
    </select>
  )
}

export function RefreshButton({ refreshing = false, onClick }: { refreshing?: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`btn-ghost btn-icon btn-refresh${refreshing ? ' is-refreshing' : ''}`} onClick={onClick} disabled={refreshing} aria-label={t('Refresh')} title={t('Refresh')}>
      <Icon name="refresh" className="refresh-icon" />
    </button>
  )
}
