import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')

const types = readFileSync(join(root, 'src/types.ts'), 'utf8')
const context = readFileSync(join(root, 'src/contexts/AppContext.tsx'), 'utf8')
const sessionsTab = readFileSync(join(root, 'src/pages/SessionsTab.tsx'), 'utf8')
const detail = readFileSync(join(root, 'src/components/SessionDetailPanel.tsx'), 'utf8')
const settings = readFileSync(join(root, 'src/pages/SettingsPage.tsx'), 'utf8')
const css = readFileSync(join(root, 'src/App.css'), 'utf8')
const zh = readFileSync(join(root, 'src/i18n/zh.ts'), 'utf8')

describe('Session evaluation types', () => {
  test('SessionOutcome type is defined', () => {
    assert.match(types, /export type SessionOutcome/)
    assert.match(types, /'solved'/)
    assert.match(types, /'partial'/)
    assert.match(types, /'failed'/)
    assert.match(types, /'stuck'/)
    assert.match(types, /'no_op'/)
    assert.match(types, /'unknown'/)
  })

  test('SessionEvaluation type is defined', () => {
    assert.match(types, /export type SessionEvaluation/)
    assert.match(types, /outcome: SessionOutcome/)
    assert.match(types, /source: SessionEvaluationSource/)
    assert.match(types, /task_title_zh: string \| null/)
    assert.match(types, /evaluated_at: string \| null/)
  })

  test('SessionSummary includes evaluation field', () => {
    assert.match(types, /evaluation: SessionEvaluation \| null/)
  })

  test('EvaluationJob matches the client-initiated run contract', () => {
    assert.match(types, /export type EvaluationJob/)
    assert.match(types, /job_id: string/)
    assert.match(types, /session_id: string/)
    assert.match(types, /client_source: string/)
    assert.match(types, /status: 'running' \| 'succeeded' \| 'failed'/)
    assert.match(types, /trigger: 'auto'/)
    assert.match(types, /evaluator_type: EvaluatorType/)
    assert.match(types, /created_at: string \| null/)
    assert.match(types, /started_at: string \| null/)
    assert.match(types, /finished_at: string \| null/)
    // The queue/server metadata was removed from the wire shape.
    assert.doesNotMatch(types, /queue_position/)
    assert.doesNotMatch(types, /ahead_count/)
    assert.doesNotMatch(types, /kind\?: string/)
  })

  test('EvaluatorType covers client evaluators', () => {
    assert.match(types, /export type EvaluatorType = 'claude' \| 'codex' \| 'opencode'/)
    assert.doesNotMatch(types, /export type EvaluatorOption/)
  })
})

describe('Manual evaluation annotation UI', () => {
  test('sessions table has outcome column header and badge', () => {
    assert.match(sessionsTab, /Outcome/)
    assert.match(sessionsTab, /session-outcome-badge/)
    assert.match(sessionsTab, /getOutcomeBadge/)
  })

  test('session detail has evaluation section and manual outcome buttons', () => {
    assert.match(detail, /session-eval-section/)
    assert.match(detail, /session-eval-btn/)
    assert.match(detail, /Solved/)
    assert.match(detail, /Failed/)
  })

  test('session detail writes manual annotations through session routes', () => {
    assert.match(detail, /\/sessions\/\$\{encodeURIComponent\(session\.session_id\)\}\/evaluation/)
    assert.match(detail, /method: 'PUT'/)
    assert.match(detail, /method: 'DELETE'/)
    assert.doesNotMatch(detail, /\/local\//)
  })

  test('session detail notifies parent on manual evaluation updates', () => {
    assert.match(detail, /onEvaluationUpdate/)
    assert.match(detail, /onEvaluationUpdate\(newEval\)/)
    assert.match(sessionsTab, /handleEvaluationUpdate/)
    assert.match(sessionsTab, /setSessions/)
  })

  test('session detail refreshes derived data after persistence succeeds', () => {
    assert.match(detail, /onEvaluationPersisted/)
    assert.match(detail, /if \(!response\.ok\) throw new Error/)
    assert.match(detail, /onEvaluationPersisted\?\.\(\)/)
    assert.match(sessionsTab, /onEvaluationPersisted=\{refreshModelEffectiveness\}/)
  })
})

describe('Client-run evaluation reporting', () => {
  test('sessions tab polls active client evaluation runs', () => {
    assert.match(sessionsTab, /fetch\('\/evaluation-jobs\/active'\)/)
    assert.match(sessionsTab, /setActiveEvaluationJobs/)
    assert.match(sessionsTab, /setTimeout\(pollActiveEvaluationJobs, 2000\)/)
  })

  test('finished runs trigger the refresh callbacks', () => {
    assert.match(sessionsTab, /previousJobIds/)
    assert.match(sessionsTab, /some\(\(jobId\) => !nextJobIds\.has\(jobId\)\)/)
    assert.match(sessionsTab, /requestUsageRefresh\(\)/)
    assert.match(sessionsTab, /refreshModelEffectiveness\(\)/)
  })

  test('sessions tab shows an informational running panel without queue controls', () => {
    assert.match(sessionsTab, /evaluator-active-panel/)
    assert.match(sessionsTab, /t\('Running Evaluations'\)/)
    assert.match(sessionsTab, /evaluation-job-row/)
    assert.doesNotMatch(sessionsTab, /evaluator-queue/)
    assert.doesNotMatch(sessionsTab, /queue_position/)
    assert.doesNotMatch(sessionsTab, /updateQueuedEvaluationJobEvaluator/)
    assert.doesNotMatch(sessionsTab, /Evaluator Queue/)
  })

  test('running rows render the raw evaluator name and start time', () => {
    assert.match(sessionsTab, /\{t\('Evaluator'\)\}: \{job\.evaluator_type\}/)
    assert.match(sessionsTab, /job\.started_at/)
    assert.doesNotMatch(sessionsTab, /evaluatorType \? evaluatorType : 'Codex'/)
  })

  test('session detail loads run history from the session evaluation-jobs route', () => {
    assert.match(detail, /\/sessions\/\$\{encodeURIComponent\(session\.session_id\)\}\/evaluation-jobs/)
    assert.match(detail, /data: \{ jobs: EvaluationJob\[\] \}/)
    assert.match(detail, /setEvaluationJobHistory\(data\.jobs \|\| \[\]\)/)
    assert.match(detail, /evaluationJobHistory\.map/)
  })

  test('run history reports status, evaluator, trigger and error only', () => {
    assert.match(detail, /job\.status === 'running' \? t\('Running'\)/)
    assert.match(detail, /t\('Succeeded'\)/)
    assert.match(detail, /\{job\.evaluator_type\}/)
    assert.match(detail, /\{t\('Auto'\)\}/)
    assert.match(detail, /\{job\.error && <pre>\{job\.error\}<\/pre>\}/)
    // The server only keeps the latest outcome on sessions; runs must not
    // claim a historical outcome or expose queue positions.
    assert.doesNotMatch(detail, /job\.outcome/)
    assert.doesNotMatch(detail, /queue_position/)
    assert.doesNotMatch(detail, /evaluation-job-outcome/)
  })

  test('run history refreshes when the session run completes', () => {
    assert.match(detail, /wasRunning && !activeJobRunning/)
    assert.match(detail, /void loadEvaluationJobHistory\(\)/)
  })
})

describe('LLM evaluation plumbing is removed', () => {
  test('session detail has no manual LLM start or poll controls', () => {
    assert.doesNotMatch(detail, /Evaluate with LLM/)
    assert.doesNotMatch(detail, /evaluate-with-llm/)
    assert.doesNotMatch(detail, /\/local\/poll/)
    assert.doesNotMatch(detail, /selectedEvaluatorType/)
    assert.doesNotMatch(detail, /Restart/)
    assert.doesNotMatch(detail, /session-eval-btn-primary/)
  })

  test('AppContext carries no evaluator globals or default fetching', () => {
    assert.doesNotMatch(context, /evaluationEvaluator/)
    assert.doesNotMatch(context, /evaluationEvaluators/)
    assert.doesNotMatch(context, /global_evaluator_type/)
    assert.doesNotMatch(context, /\/local\//)
    assert.doesNotMatch(context, /\/config\/evaluation/)
  })

  test('settings page has no evaluator default selector', () => {
    assert.doesNotMatch(settings, /evaluationEvaluator/)
    assert.doesNotMatch(settings, /handleEvaluationEvaluatorChange/)
    assert.doesNotMatch(settings, /Global default/)
    assert.doesNotMatch(settings, /Used for future automatic evaluator jobs\./)
  })

  test('useSettingsData hook was deleted with its evaluator logic', () => {
    assert.equal(existsSync(join(root, 'src/hooks/useSettingsData.ts')), false)
  })
})

describe('Session evaluation CSS', () => {
  test('outcome badge and manual button styles exist', () => {
    assert.match(css, /\.session-outcome-badge/)
    assert.match(css, /\.session-outcome-solved/)
    assert.match(css, /\.session-outcome-failed/)
    assert.match(css, /\.session-eval-btn/)
    assert.match(css, /\.session-eval-buttons/)
    assert.match(css, /\.session-eval-section/)
  })

  test('informational run styles exist', () => {
    assert.match(css, /\.session-evaluation-job-badge/)
    assert.match(css, /\.evaluator-active-panel/)
    assert.match(css, /\.evaluation-job-running-pulse/)
    assert.match(css, /\.evaluation-job-history/)
  })

  test('queue and dead LLM control styles are gone', () => {
    assert.doesNotMatch(css, /evaluator-queue/)
    assert.doesNotMatch(css, /evaluation-job-status-queued/)
    assert.doesNotMatch(css, /evaluation-job-outcome/)
    assert.doesNotMatch(css, /session-eval-btn-primary/)
    assert.doesNotMatch(css, /session-evaluator-picker/)
    assert.doesNotMatch(css, /evaluation-warning/)
  })
})

describe('Session evaluation i18n', () => {
  test('evaluation translations exist for the client-run contract', () => {
    assert.match(zh, /'Solved'/)
    assert.match(zh, /'Failed'/)
    assert.match(zh, /'Evaluation'/)
    assert.match(zh, /'Running Evaluations'/)
    assert.match(zh, /'Evaluator'/)
    assert.match(zh, /'Evaluating\.\.\.'/)
    assert.match(zh, /'Running'/)
    assert.match(zh, /'Succeeded'/)
    assert.match(zh, /'Evaluator Jobs'/)
  })

  test('dead LLM and queue translations are gone', () => {
    assert.doesNotMatch(zh, /'Evaluate with LLM'/)
    assert.doesNotMatch(zh, /'Evaluator Queue'/)
    assert.doesNotMatch(zh, /'Global default'/)
    assert.doesNotMatch(zh, /'Position'/)
    assert.doesNotMatch(zh, /'Restart'/)
  })
})

describe('Sessions infinite scroll', () => {
  test('scroll handler uses ref guard to prevent double page increments', () => {
    assert.match(sessionsTab, /loadingMoreRef\.current/)
    assert.match(sessionsTab, /loadingMoreRef = useRef\(false\)/)
  })
})
