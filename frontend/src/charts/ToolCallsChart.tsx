import { appendModels } from '../modelFilter'
import { useEffect, useRef, useState } from 'react'
import { toolColor } from '../utils'
import { HorizontalBarChart } from './HorizontalBarChart'
import type { BarItem, Metric } from './HorizontalBarChart'
import { t } from '../i18n/index.ts'
import { ReadState } from '../components/ReadState'

type ToolCallRow = {
  tool_name: string
  count: number
  total_duration_ms?: number | null
  avg_duration_ms?: number | null
}

type ToolMetric = Extract<Metric, 'count' | 'totalTime' | 'avgTime'>

// A stable default: a fresh {} per render would re-run the fetch effect forever.
const NO_FILTERS = {}

export function ToolCallsChart({
  filterParams = NO_FILTERS,
}: {
  filterParams?: {
    provider?: string
    model?: string[] | null
    client_source?: string | null
    device_id?: string | null
    since?: string | null
    view?: string
  }
}) {
  // null while a new view loads: the previous range's rows must not show
  // under it. A refresh of the same view keeps them (dimmed by the page).
  const [rows, setRows] = useState<ToolCallRow[] | null>(null)
  const view = filterParams.view ?? filterParams
  const viewRef = useRef<unknown>(view)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [metric, setMetric] = useState<ToolMetric>('count')

  useEffect(() => {
    if (viewRef.current !== view) { viewRef.current = view; setRows(null) }
    setError(false)
    const controller = new AbortController()
    async function fetchToolCalls() {
      try {
        const url = new URL('/usage/by-tool', window.location.origin)
        if (filterParams.provider) url.searchParams.set('provider', filterParams.provider)
        appendModels(url.searchParams, filterParams.model)
        if (filterParams.client_source) url.searchParams.set('client_source', filterParams.client_source)
        if (filterParams.device_id) url.searchParams.set('device_id', filterParams.device_id)
        if (filterParams.since) url.searchParams.set('since', filterParams.since)

        const res = await fetch(url.toString(), { signal: controller.signal })
        if (!res.ok) throw new Error(String(res.status))
        const data = await res.json()
        if (!controller.signal.aborted) setRows(data)
      } catch {
        if (!controller.signal.aborted) setError(true)
      }
    }
    fetchToolCalls()
    return () => controller.abort()
  }, [filterParams, attempt, view])

  const items: BarItem[] = (rows ?? []).map(row => ({
    name: row.tool_name,
    icon: null,
    color: toolColor(row.tool_name),
    tokens: row.count,
    cost: 0,
    totalTime: row.total_duration_ms ?? 0,
    avgTime: row.avg_duration_ms ?? 0,
  }))

  return (
    <HorizontalBarChart
      title={t('Tool Calls')}
      items={items}
      toolBadges
      metric={metric}
      status={error || rows === null ? <ReadState error={error ? t('Failed to load tool calls') : null} onRetry={() => setAttempt(a => a + 1)} /> : undefined}
    >
      <div className="top-list-controls">
        <div className="tab-toggle compact">
          {(['count', 'totalTime', 'avgTime'] as ToolMetric[]).map(m => (
            <button
              key={m}
              className={`tab-toggle-btn ${metric === m ? 'active' : ''}`}
              onClick={() => setMetric(m)}
            >
              {m === 'count' ? t('Count') : m === 'totalTime' ? t('Total time') : t('Avg time')}
            </button>
          ))}
        </div>
      </div>
    </HorizontalBarChart>
  )
}
