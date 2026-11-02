import { useState } from 'react'
import { useApp } from '../contexts/AppContext'
import { useDashboardData } from '../hooks/useDashboardData'
import { useOnboarding } from '../hooks/useOnboarding'
import { useDashboardAgents } from '../hooks/useDashboardAgents'
import { ModelSelector } from '../ModelSelector'
import { DeviceSelect } from '../components/DeviceSelect'
import { DATE_RANGE_OPTIONS } from '../dateRange.ts'
import type { DateRangeOption } from '../types'
import { OverviewTab } from './OverviewTab'
import { SessionsTab } from './SessionsTab'
import { t } from '../i18n/index.ts'
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
    dashboardInitialLoading, dashboardRefreshing,
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

  const [dashboardTab, setDashboardTab] = useState<'overview' | 'sessions'>('overview')

  return (
    <>
      {totalTrackedEvents !== 0 && (
      <div className="dashboard-filter-row" style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '8px', marginBottom: '16px' }}>
          <select
            id="dashboard-date-range"
            name="dateRange"
            aria-label={t('Date Range')}
            className="input-plain"
            value={dateRange}
            onChange={(e) => { setDateRange(e.target.value as DateRangeOption) }}
          >
            {DATE_RANGE_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{t(o.label)}</option>
            ))}
          </select>
          <ModelSelector
            activeFilter={activeFilter}
            summary={selectorSummary}
            providerColors={providerColors}
            onChange={(f) => { setActiveFilter(f) }}
          />
          <select
            id="dashboard-source-filter"
            name="sourceFilter"
            aria-label={t('Source Filter')}
            className="input-plain"
            value={activeSource || ''}
            onChange={(e) => { setActiveSource(e.target.value || null) }}
          >
            <option value="">{t('All Sources')}</option>
            {sources.map(source => (
              <option key={source} value={source}>{source}</option>
            ))}
          </select>
          <DeviceSelect value={activeDevice} onChange={setActiveDevice} />
          <button
            className={`btn-ghost btn-refresh ${dashboardRefreshing ? 'is-refreshing' : ''}`}
            onClick={requestUsageRefresh}
            disabled={dashboardRefreshing}
            aria-label={t('Refresh')}
            title={t('Refresh')}
          >
            <span className="refresh-icon">↻</span>
          </button>
        </div>
      )}
      <div className="dashboard-tabs" style={{ display: 'flex', gap: '4px', borderBottom: '1px solid var(--border-color)', marginBottom: '16px' }}>
        <button className={`dashboard-tab ${dashboardTab === 'overview' ? 'active' : ''}`} onClick={() => setDashboardTab('overview')}>{t('Overview')}</button>
        <button className={`dashboard-tab ${dashboardTab === 'sessions' ? 'active' : ''}`} onClick={() => setDashboardTab('sessions')}>{t('Sessions')}</button>
      </div>
      {dashboardTab === 'overview' && (
        <OverviewTab
          theme={theme}
          summary={summary}
          dailyUsage={dailyUsage}
          heatmapData={heatmapData}
          dashboardInitialLoading={dashboardInitialLoading}
          dashboardRefreshing={dashboardRefreshing}
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
      )}
      {dashboardTab === 'sessions' && (
        <SessionsTab
          onNavigateToLogs={onNavigateToLogs}
        />
      )}
    </>
  )
}
