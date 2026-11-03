import { useCallback, useEffect, useState } from 'react'
import type { DeviceRow, WebSession } from '../types'

export function useDevices(active: boolean, includeRevoked = false) {
  const [devices, setDevices] = useState<DeviceRow[] | null>(null)
  const [webSessions, setWebSessions] = useState<WebSession[] | null>(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)

  const refresh = useCallback(() => {
    setReloadKey(key => key + 1)
  }, [])

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    // A retry shows as loading, not as the error it is replacing.
    setError(false)
    setLoading(true)
    async function fetchDevices() {
      try {
        const response = await fetch(includeRevoked ? '/auth/devices?include_revoked=true' : '/auth/devices', { signal: controller.signal })
        if (!response.ok) throw new Error(String(response.status))
        const data = await response.json()
        setWebSessions(Array.isArray(data.web_sessions) ? data.web_sessions : [])
        setDevices(Array.isArray(data.devices) ? data.devices : [])
      } catch {
        if (!controller.signal.aborted) setError(true)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void fetchDevices()
    return () => controller.abort()
  }, [active, includeRevoked, reloadKey])

  return { devices, webSessions, error, loading, refresh }
}
