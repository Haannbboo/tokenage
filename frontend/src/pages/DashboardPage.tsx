import { useApp } from '../contexts/AppContext'
import { useDashboardData } from '../hooks/useDashboardData'
import { useOnboarding } from '../hooks/useOnboarding'
import { useDashboardAgents } from '../hooks/useDashboardAgents'
import { ModelSelector } from '../ModelSelector'
import { DeviceSelect } from '../components/DeviceSelect'
import { DateRangeFilter, PageHeader, RefreshButton, SourceFilter } from '../components/PageHeader'
import { OverviewTab } from './OverviewTab'
type Props = {
  onNavigateToLogs: (filters?: { sessionFilter?: string }) => void
}

export function DashboardPage({ onNavigateToLogs }: Props) {
  const {
    theme, error, requestUsageRefresh,
    activeFilter, setActiveFilter, activeSource, setActiveSource, activeDevice, setActiveDevice,
    dateRange, setDateRange,
  } = useApp()
  const { localAgents, setupDiagnostics } = useDashboardAgents()

  // Dashboard data hook
  const {
    summary, selectorSummary, dailyUsage, heatmapData, totalTrackedEvents, sources,
    dashboardInitialLoading, dashboardRefreshing, summaryPending, dashboardError, dailyError, dailyPending, heatmapError, heatmapPending,
    providerColors, dashboardFilterParams, totals,
  } = useDashboardData()

  // Onboarding hook
  const {
    verifyPhase,
    verificationResult,
    copiedOnboardingCommand,
    armOnboardingVerification,
    resetVerification,
    showFirstRunOnboarding,
    setupConfiguredAgents,
    setupSummaryText,
    setupSummaryColor,
    verifyTimeoutGuidance,
  } = useOnboarding({
    totalTrackedEvents,
    onFirstEvent: requestUsageRefresh,
    localAgents,
    setupDiagnostics,
  })

  return (
    <>
      <PageHeader title="Dashboard" description="Spend and token usage across all agents.">
        {totalTrackedEvents !== 0 && <>
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
        verifyPhase={verifyPhase}
        verificationResult={verificationResult}
        copiedOnboardingCommand={copiedOnboardingCommand}
        armOnboardingVerification={armOnboardingVerification}
        resetVerification={resetVerification}
        setupConfiguredAgents={setupConfiguredAgents}
        setupSummaryText={setupSummaryText}
        setupSummaryColor={setupSummaryColor}
        verifyTimeoutGuidance={verifyTimeoutGuidance}
        setupDiagnostics={setupDiagnostics}
        localAgents={localAgents}
        sources={sources}
        error={error}
        onNavigateToLogs={onNavigateToLogs}
      />
    </>
  )
}
