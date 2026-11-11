import { useApp } from '../contexts/AppContext'
import { useDashboardData } from '../hooks/useDashboardData'
import { useOnboarding } from '../hooks/useOnboarding'
import { ModelSelector } from '../ModelSelector'
import { DeviceSelect } from '../components/DeviceSelect'
import { DateRangeFilter, PageHeader, RefreshButton, SourceFilter } from '../components/PageHeader'
import { OverviewTab } from './OverviewTab'
type Props = {
  onNavigateToLogs: (filters?: { sessionFilter?: string }) => void
}

export function DashboardPage({ onNavigateToLogs }: Props) {
  const {
    theme, requestUsageRefresh,
    activeFilter, setActiveFilter, activeSource, setActiveSource, activeDevice, setActiveDevice,
    dateRange, setDateRange,
  } = useApp()

  // Dashboard data hook
  const {
    summary, selectorSummary, dailyUsage, heatmapData, totalTrackedEvents, sources,
    dashboardInitialLoading, dashboardRefreshing, summaryPending, dashboardError, dailyError, dailyPending, heatmapError, heatmapPending,
    providerColors, dashboardFilterParams, totals,
  } = useDashboardData()

  const { verificationResult, showFirstRunOnboarding, dismissOnboarding } = useOnboarding(
    totalTrackedEvents, requestUsageRefresh,
  )

  return (
    <>
      <PageHeader title="Dashboard" description="Spend and token usage across all agents.">
        {!showFirstRunOnboarding && <>
          <DateRangeFilter value={dateRange} onChange={setDateRange} />
          <ModelSelector
            activeFilter={activeFilter}
            summary={selectorSummary}
            providerColors={providerColors}
            onChange={(f) => { setActiveFilter(f) }}
          />
          <SourceFilter value={activeSource} sources={sources} onChange={setActiveSource} />
          <DeviceSelect value={activeDevice} onChange={setActiveDevice} />
          <RefreshButton refreshing={dashboardRefreshing} onClick={requestUsageRefresh} />
        </>}
      </PageHeader>
      <OverviewTab
        theme={theme}
        summary={summary}
        dailyUsage={dailyUsage}
        heatmapData={heatmapData}
        dashboardInitialLoading={dashboardInitialLoading}
        dashboardRefreshing={dashboardRefreshing}
        summaryPending={summaryPending}
        dashboardError={dashboardError}
        dailyError={dailyError}
        dailyPending={dailyPending}
        heatmapError={heatmapError}
        heatmapPending={heatmapPending}
        onRetry={requestUsageRefresh}
        dateRange={dateRange}
        dashboardFilterParams={dashboardFilterParams}
        totals={totals}
        showFirstRunOnboarding={showFirstRunOnboarding}
        verificationResult={verificationResult}
        onFinishOnboarding={dismissOnboarding}
        onNavigateToLogs={onNavigateToLogs}
      />
    </>
  )
}
