import { useEffect, useState } from 'react'

type VersionData = {
  name: string
  version: string
  /** The API's configured address; the served install.sh is preset with it. */
  server_url?: string | null
  installer_available?: boolean
}

export function useVersion() {
  // null: loading; undefined: all attempts failed.
  const [versionData, setVersionData] = useState<VersionData | null | undefined>(null)

  useEffect(() => {
    const controller = new AbortController()

    async function fetchVersion() {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const response = await fetch('/version', { signal: controller.signal })
          if (response.ok) {
            setVersionData(await response.json())
            return
          }
        } catch {}
        if (controller.signal.aborted) return
      }
      setVersionData(undefined)
    }

    void fetchVersion()
    return () => controller.abort()
  }, [])

  return versionData
}
