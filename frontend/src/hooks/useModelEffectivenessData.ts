import { useCallback, useEffect, useState } from 'react'
import type { DateRangeOption, ModelEffectivenessResponse } from '../types'
import { getSinceDate } from '../dateRange.ts'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'

export function useModelEffectivenessData(opts: {
  activeSource: string | null
  dateRange: DateRangeOption
  hideNoop?: boolean
}) {
  const { refreshTrigger, setError } = useApp()
  const [modelEffectiveness, setModelEffectiveness] =
    useState<ModelEffectivenessResponse>({ groups: [] })
  const [modelEffectivenessLoading, setModelEffectivenessLoading] = useState(true)
  const [modelEffectivenessRefreshKey, setModelEffectivenessRefreshKey] = useState(0)
  const refreshModelEffectiveness = useCallback(() => {
    setModelEffectivenessRefreshKey(key => key + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()

    async function fetchModelEffectivenessData() {
      setModelEffectivenessLoading(true)
      try {
        const since = getSinceDate(opts.dateRange)

        const url = new URL('/model-effectiveness', window.location.origin)
        url.searchParams.set('group_by', 'model')
        if (opts.activeSource) url.searchParams.set('client_source', opts.activeSource)
        if (since) url.searchParams.set('since', since)
        if (opts.hideNoop) url.searchParams.set('hide_noop', 'true')

        const response = await fetch(url.toString(), { signal: controller.signal })
        if (!response.ok) throw new Error(t('Failed to fetch model effectiveness data'))
        setModelEffectiveness(await response.json() as ModelEffectivenessResponse)
      } catch (err) {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : t('Unknown error'))
      } finally {
        setModelEffectivenessLoading(false)
      }
    }

    void fetchModelEffectivenessData()
    return () => controller.abort()
  }, [opts.activeSource, opts.dateRange, opts.hideNoop, refreshTrigger, modelEffectivenessRefreshKey, setError])

  return { modelEffectiveness, modelEffectivenessLoading, refreshModelEffectiveness }
}
