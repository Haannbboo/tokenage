import { useState, useEffect } from 'react'
import type { UsageRow } from '../types'

// Installing the client can take minutes, so the check runs until the first
// request arrives rather than timing out.
const POLL_MS = 5000

/** First-run guide state: shown while nothing is tracked; checks on its own. */
export function useOnboarding(totalTrackedEvents: number | null, onFirstEvent: () => void) {
  const [verificationResult, setVerificationResult] = useState<UsageRow | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const showFirstRunOnboarding = !dismissed && (totalTrackedEvents === 0 || verificationResult !== null)
  const awaitingFirstEvent = showFirstRunOnboarding && verificationResult === null

  useEffect(() => {
    if (!awaitingFirstEvent) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let inFlight = false
    let complete = false
    const check = async () => {
      clearTimeout(timer)
      if (stopped || complete || document.hidden || inFlight) return
      inFlight = true
      try {
        const countRes = await fetch('/usage/count')
        if (countRes.ok && (await countRes.json()).total > 0) {
          const usageRes = await fetch('/usage?limit=1')
          const rows = usageRes.ok ? await usageRes.json() as UsageRow[] : []
          if (rows.length > 0) {
            complete = true
            if (!stopped) {
              setVerificationResult(rows[0])
              onFirstEvent()
            }
            return
          }
        }
      } catch {
        // Offline or a failed read: try again on the next tick.
      } finally {
        inFlight = false
      }
      if (!stopped) timer = setTimeout(check, POLL_MS)
    }
    const onVisibility = () => { void check() }
    document.addEventListener('visibilitychange', onVisibility)
    void check()
    return () => {
      stopped = true
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [awaitingFirstEvent, onFirstEvent])

  return { verificationResult, showFirstRunOnboarding, dismissOnboarding: () => setDismissed(true) }
}
