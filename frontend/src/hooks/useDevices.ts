import { useCallback, useEffect, useState } from 'react'
import type { DeviceRow } from '../types'

export function useDevices(active: boolean, includeRevoked = false) {
  const [devices, setDevices] = useState<DeviceRow[] | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const refresh = useCallback(() => {
    setReloadKey(key => key + 1)
  }, [])

  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    async function fetchDevices() {
      try {
        const response = await fetch(includeRevoked ? '/auth/devices?include_revoked=true' : '/auth/devices', { signal: controller.signal })
        if (response.ok) {
          const data = await response.json()
          setDevices(Array.isArray(data.devices) ? data.devices : [])
        }
      } catch {}
    }
    void fetchDevices()
    return () => controller.abort()
  }, [active, includeRevoked, reloadKey])

  return { devices, refresh }
}
