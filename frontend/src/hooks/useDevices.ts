import { useEffect, useState } from 'react'
import { useReadRetry } from '../components/ReadState'
import type { DeviceRow, WebSession } from '../types'

export function useDevices(active: boolean, includeRevoked = false) {
  const [devices, setDevices] = useState<DeviceRow[] | null>(null)
  const [webSessions, setWebSessions] = useState<WebSession[] | null>(null)
  // A retry keeps the failure (as 'retrying') until it settles.
  const { error, setError, attempt: reloadKey, retry: refresh } = useReadRetry()
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!active) { setError(false); return }
    const controller = new AbortController()
    setLoading(true)
    async function fetchDevices() {
      try {
        const response = await fetch(includeRevoked ? '/auth/devices?include_revoked=true' : '/auth/devices', { signal: controller.signal })
        if (!response.ok) throw new Error(String(response.status))
        const data = await response.json()
        setWebSessions(Array.isArray(data.web_sessions) ? data.web_sessions : [])
        setDevices(Array.isArray(data.devices) ? data.devices : [])
        setError(false)
      } catch {
        if (!controller.signal.aborted) setError('failed')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void fetchDevices()
    return () => controller.abort()
  }, [active, includeRevoked, reloadKey, setError])

  return { devices, webSessions, error, loading, refresh }
}
