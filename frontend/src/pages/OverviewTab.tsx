import { useState } from 'react'
import { useCountUp } from '../useCountUp'
import { TrendChart } from '../charts/TrendChart'
import { TopUsageChart } from '../charts/TopUsageChart'
import { ToolCallsChart } from '../charts/ToolCallsChart'
import { DailyHeatmap } from '../charts/DailyHeatmap'
import { CopyButton } from '../components/CopyButton'
import { StatTile } from '../components/StatTile'
import { t } from '../i18n/index.ts'
import {
  formatCompact, formatCost, formatLatency, formatNumber, formatRate, formatThroughput,
  value, getAgentDisplayName, cacheHitRate,
} from '../utils'

type OverviewTabProps = {
  theme: any
  summary: any[]
  dailyUsage: any[]
  heatmapData: any[]
  dashboardInitialLoading: boolean
  dashboardRefreshing: boolean
  dateRange: string
  dashboardFilterParams: any
  totals: any
  showFirstRunOnboarding: boolean
  verifyPhase: string
  verificationResult: any
  copiedOnboardingCommand: any
  armOnboardingVerification: (value: any) => void
  resetVerification: () => void
  setupConfiguredAgents: number
  setupSummaryText: string
  setupSummaryColor: string
  verifyTimeoutGuidance: string
  setupDiagnostics: any
  localAgents: Record<string, { found: boolean }> | null
  sources: string[]
  error: string | null
  onNavigateToLogs: (filters?: any) => void
}

export function OverviewTab({
  theme,
  summary,
  dailyUsage,
  heatmapData,
  dashboardInitialLoading,
  dashboardRefreshing,
  dateRange,
  dashboardFilterParams,
  totals,
  showFirstRunOnboarding,
  verifyPhase,
  verificationResult,
  copiedOnboardingCommand,
  armOnboardingVerification,
  resetVerification,
  setupConfiguredAgents,
  setupSummaryText,
  setupSummaryColor,
  verifyTimeoutGuidance,
  setupDiagnostics,
  localAgents,
  sources,
  error,
  onNavigateToLogs,
}: OverviewTabProps) {
  const animatedTotalTokens = useCountUp(dashboardInitialLoading ? 0 : totals.totalTokens)
  const animatedRequests = useCountUp(dashboardInitialLoading ? 0 : totals.requests)
  const animatedCost = useCountUp(dashboardInitialLoading ? 0 : totals.totalCost)
  const animatedThroughput = useCountUp(dashboardInitialLoading ? 0 : totals.avgThroughput)
  const animatedHitRate = useCountUp(
    dashboardInitialLoading ? 0 : cacheHitRate(totals.cachedTokens, totals.promptTokens, totals.cacheCreationTokens) ?? 0,
  )
  const totalInputTokens = (totals.promptTokens || 0) + (totals.cacheCreationTokens || 0)

  // One cursor for the whole row: hovering a point shows that period in every
  // tile, so the metrics can be read side by side.
  const [cursorIdx, setCursorIdx] = useState<number | null>(null)

  const costPoints = dailyUsage.map(d => ({ value: value(d.total_cost_usd), period: d.period }))
  const tokenPoints = dailyUsage.map(d => ({ value: value(d.total_tokens), period: d.period }))
  const requestPoints = dailyUsage.map(d => ({ value: d.requests, period: d.period }))
  const hitRatePoints = dailyUsage.map(d => ({
    value: cacheHitRate(d.cached_tokens, d.prompt_tokens, d.cache_creation_tokens),
    period: d.period,
  }))
  const speedPoints = dailyUsage.map(d => ({ value: value(d.avg_throughput), period: d.period }))

  return (
    <>

      {dashboardInitialLoading ? (
        <div />
      ) : showFirstRunOnboarding ? (
        <div style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '40px 24px',
          textAlign: 'center',
          gap: '24px',
        }}>
          <div style={{ maxWidth: '560px' }}>
            <div style={{ fontSize: '28px', marginBottom: '8px' }}>
              {t('No traffic tracked yet')}
            </div>
            <div style={{ fontSize: '15px', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              {t('Run one test command below. When tokenage sees the request, usage, cost, and latency will appear here.')}
            </div>
          </div>

          {/* Step 1: Bootstrap */}
          <div style={{ width: '100%', maxWidth: '680px', textAlign: 'left' }}>
            <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-muted)', marginBottom: '12px', textTransform: 'uppercase' }}>
              {t('Step 1: Bootstrap')}
            </div>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '10px 16px',
              borderRadius: '8px',
              background: 'var(--surface-hover)',
              border: '1px solid var(--border-color)',
            }}>
              <code style={{ fontSize: '13px', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>tokenage bootstrap</code>
              <CopyButton
                text="tokenage bootstrap"
                style={{ fontSize: '11px', padding: '4px 10px', whiteSpace: 'nowrap' }}
                idleLabel={`📋 ${t('Copy')}`}
                copiedLabel={`✓ ${t('Copied!')}`}
              />
            </div>
          </div>

          {/* Step 2: Run a test command */}
          <div style={{ width: '100%', maxWidth: '680px', textAlign: 'left' }}>
            <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-muted)', marginBottom: '12px', textTransform: 'uppercase' }}>
              {t('Step 2: Run a test command')}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {[
                { cmd: 'tokenage claude', source: 'Claude Code' },
                { cmd: 'tokenage codex exec "hello"', source: 'Codex' },
              ].map(({ cmd, source }) => (
                <div key={cmd} style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  padding: '10px 16px',
                  borderRadius: '8px',
                  background: 'var(--surface-hover)',
                  border: '1px solid var(--border-color)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-muted)', minWidth: '80px' }}>{source}</span>
                    <code style={{ fontSize: '13px', fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{cmd}</code>
                  </div>
                  <CopyButton
                    text={cmd}
                    style={{ fontSize: '11px', padding: '4px 10px', whiteSpace: 'nowrap' }}
                    idleLabel={`📋 ${t('Copy')}`}
                    copiedLabel={`✓ ${t('Copied!')}`}
                    onCopied={() => armOnboardingVerification({ source, command: cmd })}
                  />
                </div>
              ))}
            </div>
          </div>

          {/* Step 3: Wait for event */}
          <div style={{ width: '100%', maxWidth: '680px', textAlign: 'left' }}>
            <div style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-muted)', marginBottom: '12px', textTransform: 'uppercase' }}>
              {t('Step 3: Wait for event')}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
              {verificationResult ? (
                <>
                  <div style={{
                    padding: '8px 12px',
                    borderRadius: '6px',
                    background: 'var(--icon-green-bg)',
                    color: 'var(--color-green)',
                    fontWeight: 600,
                    fontSize: '13px',
                  }}>
                    {t('Tracking works. Your first request is recorded.')}
                  </div>
                  <div style={{ fontSize: '12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('Source:')}</span> {verificationResult.client_source || '—'}</div>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('Model:')}</span> {verificationResult.model || '—'}</div>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('Tokens:')}</span> {formatNumber(verificationResult.prompt_tokens)} {t('In:')} / {formatNumber(verificationResult.completion_tokens)} {t('Out:')}</div>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('Cost:')}</span> {formatCost(value(verificationResult.total_cost_usd))}</div>
                    <div><span style={{ color: 'var(--text-muted)' }}>{t('Latency:')}</span> {formatLatency(verificationResult.latency_ms)}</div>
                  </div>
                  <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                    <button className="btn-primary" onClick={() => onNavigateToLogs()} style={{ fontSize: '12px', alignSelf: 'flex-start' }}>
                      {t('View request logs')}
                    </button>
                    <button className="btn-ghost" onClick={resetVerification} style={{ fontSize: '12px', alignSelf: 'flex-start' }}>
                      {t('Reset')}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div
                    aria-live="polite"
                    title={copiedOnboardingCommand?.command}
                    style={{
                      fontSize: '12px',
                      color: verifyPhase === 'timeout'
                        ? 'var(--color-red)'
                        : copiedOnboardingCommand && verifyPhase === 'idle'
                          ? 'var(--color-green)'
                          : 'var(--text-muted)',
                      padding: copiedOnboardingCommand && verifyPhase === 'idle' ? '8px 10px' : undefined,
                      borderRadius: copiedOnboardingCommand && verifyPhase === 'idle' ? '6px' : undefined,
                      background: copiedOnboardingCommand && verifyPhase === 'idle' ? 'var(--icon-green-bg)' : undefined,
                    }}
                  >
                    {verifyPhase === 'polling'
                      ? t('Waiting for your first event...')
                      : verifyPhase === 'timeout'
                        ? t(verifyTimeoutGuidance)
                        : copiedOnboardingCommand
                          ? <><span style={{ fontWeight: 700 }}>{copiedOnboardingCommand.source}</span>: {t('Agent command copied. Run it in your terminal — checking automatically.')}</>
                          : t('This page is checking automatically. Run a command above to generate your first event.')}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Setup health + Detected agents */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
            gap: '16px',
            width: '100%',
            maxWidth: '680px',
          }}>
            {/* Setup health */}
            <div className="panel" style={{ textAlign: 'left' }}>
              <div className="panel-tabs">
                <div className="tab active"><span>🏥</span> {t('Setup health')}</div>
              </div>
              <div className="panel-body" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                  gap: '8px',
                }}>
                  <div style={{
                    padding: '10px 12px',
                    borderRadius: '8px',
                    background: 'var(--bg-secondary)',
                  }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 700, marginBottom: '4px' }}>{t('API server')}</div>
                    <div style={{ fontSize: '13px', color: error ? 'var(--color-red)' : 'var(--color-green)', fontWeight: 700 }}>
                      {error ? t('Broken') : t('Reachable')}
                    </div>
                  </div>
                  <div style={{
                    padding: '10px 12px',
                    borderRadius: '8px',
                    background: 'var(--bg-secondary)',
                  }}>
                    <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 700, marginBottom: '4px' }}>{t('OTLP configured')}</div>
                    <div style={{ fontSize: '13px', color: setupSummaryColor, fontWeight: 700 }}>
                      {setupSummaryText}
                    </div>
                  </div>
                </div>
                {setupDiagnostics && setupConfiguredAgents === 0 && (
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    {t('No local OTLP config found yet. Run bootstrap, then run a test command above. This page checks automatically.')}
                  </div>
                )}
              </div>
            </div>

            {/* Detected agents */}
            <div className="panel" style={{ textAlign: 'left' }}>
              <div className="panel-tabs">
                <div className="tab active"><span>🤖</span> {t('Detected Agents')}</div>
              </div>
              <div className="panel-body" style={{ padding: '16px' }}>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginBottom: '12px' }}>
                  {t('Detected from your local config and available commands.')}
                </div>
                {localAgents ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                    {Object.entries(localAgents).map(([name, info]) => (
                      <div key={name} style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        gap: '12px',
                        padding: '10px 12px',
                        borderRadius: '8px',
                        background: 'var(--bg-secondary)',
                      }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0 }}>
                          <span style={{
                            width: '8px', height: '8px', borderRadius: '50%',
                            background: info.found ? 'var(--color-green)' : 'var(--text-muted)',
                            flexShrink: 0,
                          }} />
                          <div style={{ minWidth: 0 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                              <span style={{ fontWeight: 700, fontSize: '13px' }}>{getAgentDisplayName(name)}</span>
                              <span style={{ fontSize: '11px', color: info.found ? 'var(--color-green)' : 'var(--text-muted)', fontWeight: 700 }}>
                                {info.found ? t('Ready') : t('Not found')}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : sources.length > 0 ? (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                    {sources.map(src => (
                      <span key={src} style={{
                        padding: '4px 12px',
                        borderRadius: '6px',
                        background: 'var(--icon-green-bg)',
                        color: 'var(--color-green)',
                        fontWeight: 600,
                        fontSize: '13px',
                      }}>{src}</span>
                    ))}
                  </div>
                ) : (
                  <div style={{ fontSize: '13px', color: 'var(--text-muted)' }}>
                    {t('No local Agent')}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : (
      <div className={`dashboard-refresh-surface ${dashboardRefreshing ? 'is-refreshing' : ''}`}>
      <div className="widgets-grid">
          <>
        <StatTile
          label={t('Estimated Cost')}
          icon="$"
          iconClass="icon-green"
          color="var(--color-blue)"
          points={costPoints}
          animatedValue={animatedCost}
          format={(v) => formatCost(v, 2)}
          formatHover={(v) => formatCost(v, v < 0.1 ? 4 : 2)}
          cursorIdx={cursorIdx}
          onHover={setCursorIdx}
        >
          {t('Avg $/M tokens:')} <span style={{ color: 'var(--color-green)', fontWeight: 600 }}>{formatRate(totals.avgEffectivePricePerMillion)}</span>
        </StatTile>

        <StatTile
          label={t('Token Usage')}
          icon="#"
          iconClass="icon-yellow"
          color="var(--color-yellow)"
          points={tokenPoints}
          animatedValue={animatedTotalTokens}
          format={(v) => formatCompact(v)}
          cursorIdx={cursorIdx}
          onHover={setCursorIdx}
        >
          {/* cache_creation_tokens folds into "In" (fresh input, billed
              differently from cache-read) so In + Out reconciles with
              the Token Usage total above. */}
          {t('In:')} {formatCompact(totalInputTokens)} / {t('Out:')} {formatCompact(totals.completionTokens)}
        </StatTile>

        <StatTile
          label={t('Requests')}
          icon="↑"
          iconClass="icon-green"
          color="var(--color-pink)"
          points={requestPoints}
          animatedValue={animatedRequests}
          format={(v) => formatNumber(v)}
          cursorIdx={cursorIdx}
          onHover={setCursorIdx}
        >
          {t('Avg:')} <span style={{ color: 'var(--color-purple)', fontWeight: 600 }}>{formatCompact(totals.avgTokensPerRequest)} {t('tokens/req')}</span>
        </StatTile>

        <StatTile
          label={t('Cache Hit Rate')}
          icon="⚡"
          iconClass="icon-green"
          color="var(--color-green)"
          points={hitRatePoints}
          animatedValue={animatedHitRate}
          format={(v) => `${v.toFixed(1)}%`}
          scale="relative"
          cursorIdx={cursorIdx}
          onHover={setCursorIdx}
        >
          {t('Cached:')} {formatCompact(totals.cachedTokens)} {t('of')} {formatCompact(totalInputTokens)} {t('tokens')}
        </StatTile>

        <StatTile
          label={t('Avg Speed')}
          icon="🚀"
          iconClass="icon-purple"
          color="var(--color-purple)"
          points={speedPoints}
          animatedValue={animatedThroughput}
          format={(v) => formatThroughput(v)}
          cursorIdx={cursorIdx}
          onHover={setCursorIdx}
        />
          </>
      </div>

      <div className="overview-split-row" style={{ marginBottom: '24px' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <TopUsageChart
            summary={summary}
            theme={theme}
            filterParams={dashboardFilterParams}
          />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <ToolCallsChart filterParams={dashboardFilterParams} />
        </div>
      </div>

      <div className="overview-split-row overview-trend-row">
        <div style={{ flex: '1 1 0', minWidth: 0 }}>
          <TrendChart
            data={dailyUsage}
            title={`${dateRange === '24h' ? t('Hourly Usage Trend') : t('Daily Usage Trend')}`}
            granularity={dateRange === '24h' ? 'hour' : 'day'}
            periodCount={dateRange === '24h' ? 24 : dateRange === '7d' ? 7 : dateRange === '30d' ? 30 : 365}
            showDots={dateRange !== 'all'}
          />
        </div>
        <div style={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', gap: '16px' }}>
            <DailyHeatmap mode="activity" data={heatmapData} />
            <DailyHeatmap mode="success-rate" data={heatmapData} />
          </div>
      </div>
      </div>

      )}

    </>
  )
}
