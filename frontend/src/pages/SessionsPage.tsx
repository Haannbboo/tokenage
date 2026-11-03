import { useApp } from '../contexts/AppContext'
import { DateRangeFilter, PageHeader, RefreshButton, SourceFilter } from '../components/PageHeader'
import { DeviceSelect } from '../components/DeviceSelect'
import { useDashboardData } from '../hooks/useDashboardData'
import { ModelSelector } from '../ModelSelector'
import { SessionsTab } from './SessionsTab'

export function SessionsPage({ onNavigateToLogs }: { onNavigateToLogs: (filters?: { sessionFilter?: string }) => void }) {
  const {
    dateRange, setDateRange, activeFilter, setActiveFilter, activeSource, setActiveSource,
    activeDevice, setActiveDevice, requestUsageRefresh,
  } = useApp()
  // The sessions API honors date, model, source and device, so all four show here.
  const { selectorSummary, sources, providerColors } = useDashboardData({ includeDaily: false, includeHeatmap: false, includeCount: false })
  return <>
    <PageHeader title="Sessions" description="Every agent session with its requests and cost.">
      <DateRangeFilter value={dateRange} onChange={setDateRange} />
      <ModelSelector activeFilter={activeFilter} summary={selectorSummary} providerColors={providerColors} onChange={setActiveFilter} />
      <SourceFilter value={activeSource} sources={sources} onChange={setActiveSource} />
      <DeviceSelect value={activeDevice} onChange={setActiveDevice} />
      <RefreshButton onClick={requestUsageRefresh} />
    </PageHeader>
    <SessionsTab onNavigateToLogs={onNavigateToLogs} />
  </>
}
