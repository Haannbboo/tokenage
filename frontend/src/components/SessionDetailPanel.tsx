import { useState, useEffect, useRef, useCallback } from 'react'
import { ClickToCopy } from './CopyButton'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import { formatCompact, formatCost, formatDuration, formatLatency, formatModelName, formatNumber, formatTime, value, getModelIcon, sessionTaskTitle, resolveTimezone, ToolBadge } from '../utils'
import { getModelBadgeBackgroundColor, getModelTextColor } from '../model-badge'
import type { EvaluationJob, SessionEvaluation, SessionOutcome, SessionSummary } from '../types'

// ─── Shared session detail content (used by both inline and panel) ─────────────

type SessionLogFilters = {
  onlyFailed?: boolean
  status429?: boolean
  status5xx?: boolean
  status4xx?: boolean
}

export function SessionDetailContent({
  session,
  onNavigateToLogs,
  showToast,
  onEvaluationUpdate,
  onEvaluationPersisted,
  activeEvaluationJob,
}: {
  session: SessionSummary
  onNavigateToLogs: (session: SessionSummary, filters?: SessionLogFilters) => void
  showToast?: (msg: string) => void
  onEvaluationUpdate?: (evaluation: SessionEvaluation | null) => void
  onEvaluationPersisted?: () => void
  activeEvaluationJob?: EvaluationJob | null
}) {
  const { lang, timezone } = useApp()
  const tz = resolveTimezone(timezone)
  const [localEvaluationOverride, setLocalEvaluationOverride] = useState<{ sessionId: string; evaluation: SessionEvaluation | null } | null>(null)
  const [evaluationJobHistory, setEvaluationJobHistory] = useState<EvaluationJob[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const localEvaluation = localEvaluationOverride?.sessionId === session.session_id ? localEvaluationOverride.evaluation : session.evaluation
  const displaySession = { ...session, evaluation: localEvaluation }
  const sessionTitle = sessionTaskTitle(displaySession, lang)
  const setLocalEvaluation = (evaluation: SessionEvaluation | null) => {
    setLocalEvaluationOverride({ sessionId: session.session_id, evaluation })
  }

  const historyRequestRef = useRef(0)

  const loadEvaluationJobHistory = useCallback(async () => {
    const requestId = ++historyRequestRef.current
    setHistoryLoading(true)
    try {
      const response = await fetch(`/sessions/${encodeURIComponent(session.session_id)}/evaluation-jobs`)
      if (!response.ok) throw new Error('Failed to load evaluation job history')
      const data: { jobs: EvaluationJob[] } = await response.json()
      if (requestId !== historyRequestRef.current) return
      setEvaluationJobHistory(data.jobs || [])
    } catch {
      if (requestId !== historyRequestRef.current) return
      showToast?.('Failed to load evaluation job history')
    } finally {
      if (requestId === historyRequestRef.current) setHistoryLoading(false)
    }
  }, [session.session_id, showToast])

  useEffect(() => {
    setEvaluationJobHistory([])
    void loadEvaluationJobHistory()
  }, [session.session_id, loadEvaluationJobHistory])

  // The server records latest job state; refetch history when this session's
  // client evaluation finishes so the run list stops showing it as running.
  const activeJobRunning = activeEvaluationJob?.status === 'running'
  const previousActiveJobRunningRef = useRef(activeJobRunning)
  useEffect(() => {
    const wasRunning = previousActiveJobRunningRef.current
    previousActiveJobRunningRef.current = activeJobRunning
    if (wasRunning && !activeJobRunning) {
      void loadEvaluationJobHistory()
    }
  }, [activeJobRunning, loadEvaluationJobHistory])

  const updateEvaluation = async (outcome: SessionOutcome | 'reset') => {
    const prev = localEvaluation
    const newEval: SessionEvaluation | null =
      outcome === 'reset'
        ? null
        : {
            session_id: session.session_id,
            outcome,
            source: 'manual',
            confidence: null,
            task_title: null,
            task_title_zh: null,
            summary: null,
            evidence: ['User marked outcome manually'],
            failure_reason: null,
            evaluated_at: new Date().toISOString(),
          }

    // Optimistic update
    setLocalEvaluation(newEval)
    if (onEvaluationUpdate) onEvaluationUpdate(newEval)

    try {
      let response: Response
      if (outcome === 'reset') {
        response = await fetch(`/sessions/${encodeURIComponent(session.session_id)}/evaluation`, {
          method: 'DELETE',
        })
      } else {
        response = await fetch(`/sessions/${encodeURIComponent(session.session_id)}/evaluation`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            outcome,
            source: 'manual',
            evidence: ['User marked outcome manually'],
          }),
        })
      }
      if (!response.ok) throw new Error('Failed to update evaluation')
      onEvaluationPersisted?.()
    } catch {
      setLocalEvaluation(prev) // Revert on error
      if (onEvaluationUpdate) onEvaluationUpdate(prev)
      if (showToast) showToast('Failed to update evaluation')
    }
  }

  const outcomeLabels: Record<string, string> = { solved: 'Solved', partial: 'Partial', failed: 'Failed', stuck: 'Stuck', no_op: 'No-op' }

  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '20px', width: '100%' }}>
      <div style={{ display: 'flex', gap: '32px', flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div style={{ minWidth: '140px' }}>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Session ID')}</div>
          <div style={{ fontFamily: 'monospace', fontSize: '12px', wordBreak: 'break-all', maxWidth: '320px', color: 'var(--text-primary)' }}>
            {showToast ? (
              <ClickToCopy text={session.session_id} onCopy={showToast}>
                {session.session_id}
              </ClickToCopy>
            ) : (
              <span style={{ userSelect: 'all' }}>{session.session_id}</span>
            )}
          </div>
        </div>

        <div style={{ minWidth: 0, maxWidth: '320px', flex: '1 1 160px' }}>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Session Title')}</div>
          <div className="session-task-title" title={sessionTitle}>
            {sessionTitle}
          </div>
        </div>

        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Timeline')}</div>
          <div style={{ fontSize: '12px', color: 'var(--text-primary)' }}>
            <div style={{ fontWeight: 600 }}>{formatTime(session.started, tz)}</div>
            <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '2px' }}>{formatDuration(session.duration_s)} {t('duration')}</div>
          </div>
        </div>

        {'model' in session && session.model && (
          <div>
            <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Model')}</div>
            <div style={{
              padding: '4px 6px',
              borderRadius: '6px',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              fontSize: '11px',
              backgroundColor: getModelBadgeBackgroundColor(session.model),
              color: getModelTextColor(session.model),
              fontWeight: 600
            }} title={session.model}>
              {getModelIcon(session.model)}
              <span style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                maxWidth: '160px'
              }}>
                {formatModelName(session.model)}
              </span>
            </div>
          </div>
        )}

        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Cache Hit Rate')}</div>
          <div style={{ width: '120px' }}>
            {(() => {
              // cache_creation_tokens is disjoint from prompt_tokens (Anthropic
              // semantics), so it must be added to the denominator for a true hit rate.
              const totalInput = session.prompt_tokens + session.cache_creation_tokens
              const hitRate = totalInput > 0 ? (session.cached_tokens / totalInput) * 100 : 0
              return (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', marginBottom: '4px' }}>
                    <span style={{ fontWeight: 700, color: 'var(--color-green)' }}>{Math.round(hitRate)}%</span>
                    <span style={{ color: 'var(--text-muted)' }}>{formatCompact(session.cached_tokens)} {t('tokens')}</span>
                  </div>
                  <div style={{ height: '6px', background: 'var(--progress-bg)', borderRadius: '3px', overflow: 'hidden', border: '1px solid var(--border-color)' }}>
                    <div style={{ height: '100%', background: 'var(--color-green)', width: `${hitRate}%` }} />
                  </div>
                </>
              )
            })()}
          </div>
        </div>

        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Avg Throughput')}</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '4px' }}>
            <span style={{ fontSize: '18px', fontWeight: 800, color: 'var(--text-primary)' }}>
              {session.latency_sum_ms > 0 ? ((session.completion_tokens * 1000) / session.latency_sum_ms).toFixed(1) : '0.0'}
            </span>
            <span style={{ fontSize: '10px', color: 'var(--text-muted)', fontWeight: 600 }}>t/s</span>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '20px' }}>
          <div>
            <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Requests')}</div>
            <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)' }}>{formatNumber(session.request_count)}</div>
            {session.failed_requests > 0 && (
              <div
                style={{ fontSize: '10px', color: 'var(--color-red)', marginTop: '2px', fontWeight: 600, cursor: 'pointer', textDecoration: 'underline' }}
                onClick={(e) => { e.stopPropagation(); onNavigateToLogs(session, { onlyFailed: true }); }}
                title={t('View failed requests in logs')}
              >
                {session.failed_requests} {t('failed')}
              </div>
            )}
          </div>
          <div>
            <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Success Rate')}</div>
            <div
              style={{ fontSize: '14px', fontWeight: 700, color: session.failed_requests === 0 ? 'var(--color-green)' : 'var(--color-orange)', cursor: session.failed_requests > 0 ? 'pointer' : 'default', textDecoration: session.failed_requests > 0 ? 'underline' : 'none' }}
              onClick={(e) => { if (session.failed_requests > 0) { e.stopPropagation(); onNavigateToLogs(session, { onlyFailed: true }); } }}
              title={session.failed_requests > 0 ? t('View failed requests in logs') : undefined}
            >
              {session.request_count > 0 ? Math.round((session.successful_requests / session.request_count) * 100) : 0}%
            </div>
            {session.failed_requests > 0 && (
              <div
                className="stat-label"
                style={{ marginTop: '4px', display: 'flex', gap: '6px', textTransform: 'none' }}
              >
                {value(session.status_429) > 0 && (
                  <span
                    className="status-link"
                    onClick={(e) => { e.stopPropagation(); onNavigateToLogs(session, { status429: true }); }}
                  >
                    429: {session.status_429}
                  </span>
                )}
                {value(session.status_5xx) > 0 && (
                  <span
                    className="status-link"
                    onClick={(e) => { e.stopPropagation(); onNavigateToLogs(session, { status5xx: true }); }}
                  >
                    5xx: {session.status_5xx}
                  </span>
                )}
                {value(session.status_4xx) > 0 && (
                  <span
                    className="status-link"
                    onClick={(e) => { e.stopPropagation(); onNavigateToLogs(session, { status4xx: true }); }}
                  >
                    4xx: {session.status_4xx}
                  </span>
                )}
              </div>
            )}
          </div>
          <div>
            <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Cost')}</div>
            <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--color-green)' }}>{formatCost(session.total_cost_usd, 2)}</div>
          </div>
        </div>

        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Performance')}</div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <div style={{ background: 'var(--badge-success-bg)', color: 'var(--badge-success-text)', padding: '2px 10px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 }}>
              {formatLatency(session.avg_ttft_ms)} TTFT
            </div>
            <div style={{ background: 'var(--badge-error-bg)', color: 'var(--badge-error-text)', padding: '2px 10px', borderRadius: '4px', fontSize: '11px', fontWeight: 600 }}>
              {formatLatency(session.avg_latency_ms)} Latency
            </div>
          </div>
        </div>

        <div>
          <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Token Usage')}</div>
          <div style={{ width: '160px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', marginBottom: '4px' }}>
              <span style={{ fontWeight: 700, color: 'var(--text-primary)' }}>{formatCompact(session.total_tokens)}</span>
              <span style={{ color: 'var(--text-muted)' }}>{t('total')}</span>
            </div>
            <div className="has-tooltip" style={{ borderBottom: 'none', display: 'block', width: '100%' }}>
              {(() => {
                const promptUncached = Math.max(0, value(session.prompt_tokens) - value(session.cached_tokens));
                const cacheCreation = value(session.cache_creation_tokens);
                // total_tokens includes cache_creation_tokens for Anthropic sessions,
                // so it's rendered as its own segment or the bar won't fill correctly.
                const barTotal = value(session.total_tokens) || 1;
                return (
                  <>
                    <div style={{ height: '6px', background: 'var(--progress-bg)', borderRadius: '3px', overflow: 'hidden', border: '1px solid var(--border-color)', display: 'flex', width: '100%' }}>
                      <div style={{ height: '100%', background: 'var(--color-green)', width: `${(value(session.cached_tokens) / barTotal) * 100}%` }} />
                      <div style={{ height: '100%', background: 'var(--color-blue)', width: `${(promptUncached / barTotal) * 100}%`, opacity: 0.7 }} />
                      {cacheCreation > 0 && (
                        <div style={{ height: '100%', background: 'var(--color-indigo)', width: `${(cacheCreation / barTotal) * 100}%` }} />
                      )}
                      <div style={{ height: '100%', background: 'var(--color-purple)', width: `${(value(session.completion_tokens) / barTotal) * 100}%` }} />
                    </div>
                    <div className="tooltip-text" style={{ width: '180px', marginLeft: '-90px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--color-green)' }}>● {t('Cached')}:</span>
                          <span>{formatNumber(session.cached_tokens)}</span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--color-blue)' }}>● {t('Input')}:</span>
                          <span>{formatNumber(promptUncached)}</span>
                        </div>
                        {cacheCreation > 0 && (
                          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                            <span style={{ color: 'var(--color-indigo)' }}>● {t('Cache write')}:</span>
                            <span>{formatNumber(cacheCreation)}</span>
                          </div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span style={{ color: 'var(--color-purple)' }}>● {t('Output')}:</span>
                          <span>{formatNumber(session.completion_tokens)}</span>
                        </div>
                      </div>
                    </div>
                  </>
                );
              })()}
            </div>
            <div style={{ display: 'flex', gap: '8px', marginTop: '6px', fontSize: '9px', fontWeight: 600, flexWrap: 'wrap' }}>
              <span style={{ color: 'var(--color-green)' }}>● {t('Cache')}</span>
              <span style={{ color: 'var(--color-blue)' }}>● {t('In')}</span>
              {value(session.cache_creation_tokens) > 0 && (
                <span style={{ color: 'var(--color-indigo)' }}>● {t('Cache write')}</span>
              )}
              <span style={{ color: 'var(--color-purple)' }}>● {t('Out')}</span>
            </div>
          </div>
        </div>

        {session.tool_calls_json && Object.keys(session.tool_calls_json).length > 0 && (
          <div>
            <div style={{ fontSize: '10px', color: 'var(--text-muted)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 700 }}>{t('Tool Usage')}</div>
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {Object.entries(session.tool_calls_json).map(([name, count]) => (
                <ToolBadge key={name} name={name} count={count} />
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="session-eval-section">
        <div className="session-eval-label">{t('Evaluation')}</div>
        {localEvaluation && (
          <div style={{ marginBottom: '8px' }}>
            <span className={`session-outcome-badge session-outcome-${localEvaluation.outcome}`}>
              {outcomeLabels[localEvaluation.outcome] || localEvaluation.outcome}
            </span>
            <span style={{ fontSize: '11px', color: 'var(--text-muted)', marginLeft: '8px' }}>
              {t('Source')}: {localEvaluation.source === 'manual' ? t('Manual') : localEvaluation.source === 'heuristic' ? t('Heuristic') : t('LLM')}
            </span>
            {localEvaluation.evidence && localEvaluation.evidence.length > 0 && (
              <div style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: '4px' }}>
                {localEvaluation.evidence.join(', ')}
              </div>
            )}
          </div>
        )}
        <div className="session-eval-buttons">
          {(['solved', 'partial', 'failed', 'stuck', 'no_op'] as const).map((o) => (
            <button
              key={o}
              className={`session-eval-btn${localEvaluation?.outcome === o ? ` session-eval-btn-active-${o}` : ''}`}
              onClick={() => updateEvaluation(o)}
            >
              {outcomeLabels[o]}
            </button>
          ))}
          <button
            className="session-eval-btn session-eval-btn-reset"
            onClick={() => updateEvaluation('reset')}
          >
            {t('Reset')}
          </button>
        </div>
        <div className="evaluation-job-history">
          <div className="evaluation-job-history-title">{t('Evaluator Jobs')}</div>
          {historyLoading && <div className="evaluation-job-history-empty">{t('Loading...')}</div>}
          {!historyLoading && evaluationJobHistory.length === 0 && (
            <div className="evaluation-job-history-empty">{t('No evaluator jobs yet.')}</div>
          )}
          {!historyLoading && evaluationJobHistory.map((job) => {
            const failed = job.status === 'failed'
            const errorPreview = job.error?.split('\n')[0] || t('No failure reason recorded')
            return (
              <details key={job.job_id} className={`evaluation-job-history-item evaluation-job-history-${job.status}`} open={failed}>
                <summary>
                  <span className={`evaluation-job-status evaluation-job-status-${job.status}`}>
                    {job.status === 'running' ? t('Running') : job.status === 'succeeded' ? t('Succeeded') : t('Failed')}
                  </span>
                  <span>{job.evaluator_type}</span>
                  <span>{t('Auto')}</span>
                  {failed && <span className="evaluation-job-error-preview">{errorPreview}</span>}
                </summary>
                <div className="evaluation-job-detail">
                  <div>{t('Created')}: {job.created_at ? formatTime(job.created_at, tz) : '—'}</div>
                  <div>{t('Started')}: {job.started_at ? formatTime(job.started_at, tz) : '—'}</div>
                  <div>{t('Finished')}: {job.finished_at ? formatTime(job.finished_at, tz) : '—'}</div>
                  <div>{t('Evaluator')}: {job.evaluator_type}</div>
                  <div>{t('Trigger')}: {t('Auto')}</div>
                  {job.device_name && <div>{t('Device')}: {job.device_name}</div>}
                  {job.error && <pre>{job.error}</pre>}
                </div>
              </details>
            )
          })}
        </div>
      </div>
    </div>
  )
}
