import { useCallback, useState } from 'react'
import { t } from '../i18n/index.ts'

/** A read's failure as a panel tracks it. A retry turns 'failed' into
 *  'retrying', which keeps the failed panel's ReadState (and its focused Retry)
 *  mounted, pending, until the retry settles: only a success or another view
 *  clears it, with `setError(false)`. */
export type ReadError = false | 'failed' | 'retrying'

export function useReadRetry() {
  const [error, setError] = useState<ReadError>(false)
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback(() => {
    setError(e => e && 'retrying')
    setAttempt(a => a + 1)
  }, [])
  return { error, setError, attempt, retry }
}

/** Stands in for data that is still loading or failed to load, so a view never
 *  shows zeros, "No data" or another range's numbers as if they were current.
 *  While a retry runs, its button stays mounted (aria-disabled, which unlike
 *  disabled keeps focus on it) so keyboard focus is not dropped. */
export function ReadState({ error, onRetry, compact = false }: {
  error?: string | null
  onRetry?: () => void
  /** A one-line note inside a panel that still shows its other data. */
  compact?: boolean
}) {
  const [retrying, setRetrying] = useState(false)
  const button = onRetry && (error || retrying) && (
    <button
      type="button"
      className="btn-ghost"
      aria-disabled={error ? undefined : true}
      onClick={() => { if (!error) return; setRetrying(true); onRetry() }}
    >{t('Retry')}</button>
  )
  const className = `read-state${compact ? ' is-compact' : ''}`
  return error
    ? <div role="alert" className={`${className} is-error`}>{error}{button}</div>
    : <div role="status" aria-busy="true" className={className}>{t('Loading...')}{button}</div>
}
