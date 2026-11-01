import { useEffect, useState } from 'react'
import { getToolColor } from '../utils'
import { HorizontalBarChart } from './HorizontalBarChart'
import type { BarItem, Metric } from './HorizontalBarChart'
import { t } from '../i18n/index.ts'

type ToolCallRow = {
  tool_name: string
  count: number
  total_duration_ms?: number | null
  avg_duration_ms?: number | null
}

type ToolMetric = Extract<Metric, 'count' | 'totalTime' | 'avgTime'>

export function ToolCallsChart({
  filterParams = {},
}: {
  filterParams?: {
    provider?: string
    model?: string | null
    client_source?: string | null
    device_id?: string | null
    since?: string | null
  }
}) {
  const [rows, setRows] = useState<ToolCallRow[]>([])
  const [metric, setMetric] = useState<ToolMetric>('count')

  useEffect(() => {
    const controller = new AbortController()
    async function fetchToolCalls() {
      try {
        const url = new URL('/usage/by-tool', window.location.origin)
        if (filterParams.provider) url.searchParams.set('provider', filterParams.provider)
        if (filterParams.model) url.searchParams.set('model', filterParams.model)
        if (filterParams.client_source) url.searchParams.set('client_source', filterParams.client_source)
        if (filterParams.device_id) url.searchParams.set('device_id', filterParams.device_id)
        if (filterParams.since) url.searchParams.set('since', filterParams.since)

        const res = await fetch(url.toString(), { signal: controller.signal })
        if (res.ok) {
          setRows(await res.json())
        }
      } catch {
        // Ignore abort errors
      }
    }
    fetchToolCalls()
    return () => controller.abort()
  }, [filterParams])

  const items: BarItem[] = rows.map(row => {
    const c = getToolColor(row.tool_name)
    return {
      name: row.tool_name,
      icon: null,
      tokens: row.count,
      cost: 0,
      totalTime: row.total_duration_ms ?? 0,
      avgTime: row.avg_duration_ms ?? 0,
      color: c.bg,
      badgeBg: c.bg,
      badgeText: c.text,
    }
  })

  return (
    <HorizontalBarChart
      title={t('Tool Calls')}
      icon="🔧"
      items={items}
      metric={metric}
    >
      <div style={{
        padding: '8px 12px',
        borderTop: '1px solid var(--border-color)',
        display: 'flex',
        flexWrap: 'wrap',
        gap: '2px',
        background: 'var(--tab-toggle-bg)',
        borderRadius: '6px',
        paddingTop: '8px',
      }}>
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
    </HorizontalBarChart>
  )
}
