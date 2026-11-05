import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { DATE_RANGE_OPTIONS } from '../dateRange'
import { useApp } from '../contexts/AppContext'
import { DateRangeFilter, PageHeader, RefreshButton, SourceFilter } from '../components/PageHeader'
import { DeviceSelect } from '../components/DeviceSelect'
import { useDashboardData } from '../hooks/useDashboardData'
import { ModelSelector } from '../ModelSelector'
import type { DateRangeOption } from '../types'
import { SessionsTab } from './SessionsTab'

export function SessionPageHeader({ title, description, dateRangeControl }: { title: string; description: string; dateRangeControl?: { value: DateRangeOption; onChange: (value: DateRangeOption) => void } }) {
  const {
    dateRange, setDateRange, activeFilter, setActiveFilter, activeSource, setActiveSource,
    activeDevice, setActiveDevice, requestUsageRefresh,
  } = useApp()
  // The sessions API honors date, model, source and device, so all four show here.
  const { selectorSummary, sources, providerColors } = useDashboardData({ dateRange: dateRangeControl?.value, includeDaily: false, includeHeatmap: false, includeCount: false })
  return <PageHeader title={title} description={description}>
      <DateRangeFilter value={dateRangeControl?.value ?? dateRange} onChange={dateRangeControl?.onChange ?? setDateRange} />
      <ModelSelector activeFilter={activeFilter} summary={selectorSummary} providerColors={providerColors} onChange={setActiveFilter} />
      <SourceFilter value={activeSource} sources={sources} onChange={setActiveSource} />
      <DeviceSelect value={activeDevice} onChange={setActiveDevice} />
      <RefreshButton onClick={requestUsageRefresh} />
    </PageHeader>
}

export function SessionsPage({ onNavigateToLogs }: { onNavigateToLogs: (filters?: { sessionFilter?: string }) => void }) {
  const { dateRange, setDateRange } = useApp()
  const [search, setSearch] = useSearchParams()
  const incoming = DATE_RANGE_OPTIONS.find(option => option.value === search.get('range'))?.value
  const range = incoming ?? dateRange
  useEffect(() => {
    if (!search.has('range')) return
    if (incoming) setDateRange(incoming)
    setSearch(current => { const next = new URLSearchParams(current); next.delete('range'); return next }, { replace: true })
  }, [incoming, search, setSearch, setDateRange])
  return <>
    <SessionPageHeader dateRangeControl={{ value: range, onChange: setDateRange }} title="Sessions" description="Every agent session with its requests and cost." />
    <SessionsTab dateRangeOverride={range} onNavigateToLogs={onNavigateToLogs} />
  </>
}
