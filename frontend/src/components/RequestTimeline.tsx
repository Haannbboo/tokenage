import { useState } from 'react'
import { formatLatency, getToolColor, ToolBadge } from '../utils'
import { t } from '../i18n/index.ts'

export type TimelineTool = {
  tool_name: string
  tool_use_id: string
  duration_ms?: number | null
  ts?: number
  is_parallel?: boolean
}

export type TimelineCustomComponent = {
  key: string
  label: string
  duration_ms: number
  start_ms?: number
  color?: string
  badge?: string
}

export type RequestTimelineProps = {
  latencyMs?: number | null
  ttftMs?: number | null
  toolCalls?: TimelineTool[] | null
  extraComponents?: TimelineCustomComponent[]
}

export type TimelineSegment = {
  key: string
  label: string
  toolName?: string
  toolUseId?: string
  startMs: number
  durationMs: number
  color: string
}

export type TimelineRowItem = {
  id: string
  type: 'ttft' | 'tool' | 'parallel_group' | 'generation' | 'custom'
  label: string
  badge?: string
  startMs: number
  durationMs: number
  segments: TimelineSegment[]
  isParallel?: boolean
  tools?: TimelineTool[]
}

const TTFT_COLOR = '#38bdf8' // Sky blue
const GEN_COLOR = '#10b981' // Emerald green (matches reference timeline)

function barPercents(startMs: number, durationMs: number, totalMs: number) {
  const left = Math.max(0, Math.min(100, (startMs / totalMs) * 100))
  const rawWidth = (durationMs / totalMs) * 100
  const width = left >= 100 ? 0 : Math.max(0.5, Math.min(100 - left, rawWidth))
  return { left, width }
}

export function buildTimelineData({
  latencyMs,
  ttftMs,
  toolCalls,
  extraComponents = [],
}: {
  latencyMs?: number | null
  ttftMs?: number | null
  toolCalls?: TimelineTool[] | null
  extraComponents?: TimelineCustomComponent[]
}): {
  rows: TimelineRowItem[]
  untimed: TimelineTool[]
  totalMs: number
  displayTotal: number
  hasTiming: boolean
} {
  const tools = toolCalls || []
  const timedTools = tools.filter((tc) => (tc.duration_ms ?? 0) > 0)
  const untimed = tools.filter((tc) => (tc.duration_ms ?? 0) <= 0)

  const latency = Math.max(latencyMs ?? 0, 0)
  const rawTtft = Math.max(ttftMs ?? 0, 0)
  // TTFT can't exceed the request latency; clamp only when latency is known.
  const ttft = latency > 0 ? Math.min(rawTtft, latency) : rawTtft

  // Compute start offsets for timed tools
  type ToolWithTiming = TimelineTool & {
    startMs: number
    endMs: number
    durationMs: number
  }

  const toolsWithTiming: ToolWithTiming[] = []

  // Latency covers TTFT + output generation only (every source reports it
  // without tool execution), and a tool runs after the step that called it, so
  // tools are laid out from the end of generation.
  const toolsStart = Math.max(latency, ttft)
  const timestamps = timedTools.map((t) => t.ts).filter((ts): ts is number => ts != null)
  const hasDistinctTs =
    timestamps.length === timedTools.length &&
    timestamps.length > 1 &&
    new Set(timestamps).size > 1
  const sameTs = timestamps.length === timedTools.length && timestamps.length > 1 && !hasDistinctTs

  if (hasDistinctTs) {
    const minTs = Math.min(...timestamps)
    for (const tc of timedTools) {
      const dur = tc.duration_ms ?? 0
      const start = toolsStart + Math.max(0, tc.ts! - minTs)
      toolsWithTiming.push({ ...tc, startMs: start, endMs: start + dur, durationMs: dur })
    }
  } else {
    let currentOffset = toolsStart
    for (const tc of timedTools) {
      const dur = tc.duration_ms ?? 0
      toolsWithTiming.push({
        ...tc,
        startMs: currentOffset,
        endMs: currentOffset + dur,
        durationMs: dur,
      })
      // Same-timestamp or flagged tools ran in parallel; others run in sequence.
      if (!tc.is_parallel && !sameTs) currentOffset += dur
    }
  }

  // Sort tools by startMs
  toolsWithTiming.sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs)

  // Group tools into sequential rows or parallel clusters
  // Two tools overlap if max(startA, startB) < min(endA, endB)
  const toolClusters: ToolWithTiming[][] = []
  for (const tool of toolsWithTiming) {
    if (toolClusters.length === 0) {
      toolClusters.push([tool])
      continue
    }

    const lastCluster = toolClusters[toolClusters.length - 1]
    const clusterMaxEnd = Math.max(...lastCluster.map((t) => t.endMs))
    const clusterMinStart = Math.min(...lastCluster.map((t) => t.startMs))

    // Overlaps if tool starts before cluster finishes
    const overlaps = tool.startMs < clusterMaxEnd && tool.endMs > clusterMinStart

    if (overlaps || tool.is_parallel) {
      lastCluster.push(tool)
    } else {
      toolClusters.push([tool])
    }
  }

  // Calculate timeline bounds
  const toolMaxEnd =
    toolsWithTiming.length > 0 ? Math.max(...toolsWithTiming.map((t) => t.endMs)) : ttft
  const totalMs = Math.max(latency, toolMaxEnd, ttft)
  // "Total" is the drawn span: generation plus the tools that followed it.
  const displayTotal = totalMs

  const rows: TimelineRowItem[] = []

  // 1. TTFT
  if (ttft > 0) {
    const dur = Math.min(ttft, totalMs)
    rows.push({
      id: 'ttft',
      type: 'ttft',
      label: t('TTFT'),
      startMs: 0,
      durationMs: dur,
      segments: [
        {
          key: 'ttft',
          label: t('TTFT'),
          startMs: 0,
          durationMs: dur,
          color: TTFT_COLOR,
        },
      ],
    })
  }

  // 2. Extra components scheduled early (e.g. routing, queue)
  for (const extra of extraComponents.filter((c) => (c.start_ms ?? 0) <= ttft)) {
    rows.push({
      id: extra.key,
      type: 'custom',
      label: extra.label,
      badge: extra.badge,
      startMs: extra.start_ms ?? 0,
      durationMs: extra.duration_ms,
      segments: [
        {
          key: extra.key,
          label: extra.label,
          startMs: extra.start_ms ?? 0,
          durationMs: extra.duration_ms,
          color: extra.color || '#94a3b8',
        },
      ],
    })
  }

  // 3. Tool uses (TUs)
  for (const cluster of toolClusters) {
    if (cluster.length === 1) {
      // Sequential TU: one TU per line
      const tc = cluster[0]
      rows.push({
        id: `tool:${tc.tool_use_id}`,
        type: 'tool',
        label: tc.tool_name,
        startMs: tc.startMs,
        durationMs: tc.durationMs,
        tools: [tc],
        segments: [
          {
            key: `tool:${tc.tool_use_id}`,
            label: tc.tool_name,
            toolName: tc.tool_name,
            toolUseId: tc.tool_use_id,
            startMs: tc.startMs,
            durationMs: tc.durationMs,
            color: getToolColor(tc.tool_name).bg,
          },
        ],
      })
    } else {
      // Parallel TUs: stacked in a single line with stacked sub-bars
      const clusterStart = Math.min(...cluster.map((t) => t.startMs))
      const clusterEnd = Math.max(...cluster.map((t) => t.endMs))
      const clusterDuration = Math.max(clusterEnd - clusterStart, 1)

      rows.push({
        id: `parallel:${cluster.map((t) => t.tool_use_id).join(':')}`,
        type: 'parallel_group',
        label: `${t('Parallel TUs')} (${cluster.length})`,
        isParallel: true,
        startMs: clusterStart,
        durationMs: clusterDuration,
        tools: cluster,
        segments: cluster.map((tc) => ({
          key: `tool:${tc.tool_use_id}`,
          label: tc.tool_name,
          toolName: tc.tool_name,
          toolUseId: tc.tool_use_id,
          startMs: tc.startMs,
          durationMs: tc.durationMs,
          color: getToolColor(tc.tool_name).bg,
        })),
      })
    }
  }

  // 4. Output generation time: the rest of the latency after TTFT.
  const genStart = Math.max(0, Math.min(ttft, totalMs))
  const genDuration = Math.max(0, latency - genStart)
  if (genDuration > 0) {
    rows.push({
      id: 'generation',
      type: 'generation',
      label: t('Output generation'),
      startMs: genStart,
      durationMs: genDuration,
      segments: [
        {
          key: `generation:${genStart}`,
          label: t('Output generation'),
          startMs: genStart,
          durationMs: genDuration,
          color: GEN_COLOR,
        },
      ],
    })
  }

  // 5. Remaining extra components
  for (const extra of extraComponents.filter((c) => (c.start_ms ?? 0) > ttft)) {
    rows.push({
      id: extra.key,
      type: 'custom',
      label: extra.label,
      badge: extra.badge,
      startMs: extra.start_ms ?? genStart,
      durationMs: extra.duration_ms,
      segments: [
        {
          key: extra.key,
          label: extra.label,
          startMs: extra.start_ms ?? genStart,
          durationMs: extra.duration_ms,
          color: extra.color || '#94a3b8',
        },
      ],
    })
  }

  rows.sort((a, b) => a.startMs - b.startMs)

  const hasTiming = totalMs > 0 && rows.length > 0
  return { rows, untimed, totalMs, displayTotal, hasTiming }
}

export function RequestTimeline({
  latencyMs,
  ttftMs,
  toolCalls,
  extraComponents,
}: RequestTimelineProps) {
  const [expandedParallelId, setExpandedParallelId] = useState<string | null>(null)

  const { rows, untimed, totalMs, displayTotal, hasTiming } = buildTimelineData({
    latencyMs,
    ttftMs,
    toolCalls,
    extraComponents,
  })

  if (!hasTiming) return null

  return (
    <div className="request-timeline">
      <div className="request-timeline-header">
        <span className="request-timeline-title">{t('Time breakdown')}</span>
      </div>

      <div className="request-timeline-rows">
        {rows.map((row) => {
          const isParallel = row.isParallel && row.segments.length > 1
          const isExpanded = expandedParallelId === row.id

          return (
            <div key={row.id} className="request-timeline-row-group">
              <div
                className={`request-timeline-row ${isParallel ? 'is-parallel-row' : ''}`}
                onClick={isParallel ? () => setExpandedParallelId(isExpanded ? null : row.id) : undefined}
                onKeyDown={
                  isParallel
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          setExpandedParallelId(isExpanded ? null : row.id)
                        }
                      }
                    : undefined
                }
                role={isParallel ? 'button' : undefined}
                tabIndex={isParallel ? 0 : undefined}
                aria-expanded={isParallel ? isExpanded : undefined}
                style={{ cursor: isParallel ? 'pointer' : 'default' }}
                title={
                  isParallel
                    ? `${t('Parallel execution')}: ${row.segments.map((s) => `${s.label} (${formatLatency(s.durationMs)})`).join(', ')}`
                    : `${row.label}: ${formatLatency(row.durationMs)}`
                }
              >
                {/* Column 1: Label and Badges */}
                <div className="request-timeline-label-col">
                  {row.type === 'tool' && row.tools?.[0] ? (
                    <ToolBadge name={row.tools[0].tool_name} style={{ fontSize: '10px', padding: '1px 6px' }} />
                  ) : row.type === 'parallel_group' ? (
                    <div className="request-timeline-parallel-badges">
                      <span className="request-timeline-parallel-pill">{t('Parallel')}</span>
                      {row.segments.slice(0, 2).map((s) => (
                        <ToolBadge
                          key={s.key}
                          name={s.toolName || s.label}
                          style={{ fontSize: '9px', padding: '1px 4px' }}
                        />
                      ))}
                      {row.segments.length > 2 && (
                        <span className="request-timeline-more-count">+{row.segments.length - 2}</span>
                      )}
                    </div>
                  ) : (
                    <>
                      <span className="request-timeline-label">{row.label}</span>
                      {row.badge && <span className="request-timeline-badge">{row.badge}</span>}
                    </>
                  )}
                </div>

                {/* Column 2: Timeline Track */}
                <div className="request-timeline-track">
                  {!isParallel ? (
                    // Single segment bar
                    row.segments.map((segment) => {
                      const { left: leftPct, width: widthPct } = barPercents(
                        segment.startMs,
                        segment.durationMs,
                        totalMs,
                      )
                      return (
                        <div
                          key={segment.key}
                          className="request-timeline-bar-segment"
                          style={{
                            left: `${leftPct}%`,
                            width: `${widthPct}%`,
                            background: segment.color,
                          }}
                        />
                      )
                    })
                  ) : (
                    // Stacked parallel bars: each tool occupies a horizontal slice stacked vertically inside the track
                    <div className="request-timeline-parallel-stack">
                      {row.segments.map((segment, idx) => {
                        const { left: leftPct, width: widthPct } = barPercents(
                          segment.startMs,
                          segment.durationMs,
                          totalMs,
                        )
                        const heightPct = 100 / row.segments.length
                        const topPct = idx * heightPct
                        return (
                          <div
                            key={segment.key}
                            className="request-timeline-bar-stacked"
                            title={`${segment.label}: ${formatLatency(segment.durationMs)}`}
                            style={{
                              left: `${leftPct}%`,
                              width: `${widthPct}%`,
                              top: `${topPct}%`,
                              height: `${Math.max(heightPct - 1, 2)}%`,
                              background: segment.color,
                            }}
                          />
                        )
                      })}
                    </div>
                  )}
                </div>

                {/* Column 3: Duration */}
                <div className="request-timeline-duration-col">
                  {formatLatency(row.durationMs)}
                </div>
              </div>

              {/* Sub-rows for parallel group when expanded */}
              {isParallel && isExpanded && (
                <div className="request-timeline-parallel-details">
                  {row.segments.map((s) => {
                    const { left: leftPct, width: widthPct } = barPercents(
                      s.startMs,
                      s.durationMs,
                      totalMs,
                    )
                    return (
                      <div key={s.key} className="request-timeline-row is-sub-row">
                        <div className="request-timeline-label-col" style={{ paddingLeft: '8px' }}>
                          <span className="request-timeline-sub-bullet">↳</span>
                          <ToolBadge name={s.toolName || s.label} style={{ fontSize: '9px', padding: '1px 5px' }} />
                        </div>
                        <div className="request-timeline-track" style={{ height: '8px' }}>
                          <div
                            className="request-timeline-bar-segment"
                            style={{
                              left: `${leftPct}%`,
                              width: `${widthPct}%`,
                              background: s.color,
                            }}
                          />
                        </div>
                        <div className="request-timeline-duration-col" style={{ fontSize: '10px' }}>
                          {formatLatency(s.durationMs)}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Footer with untimed tools and Total Latency */}
      <div className="request-timeline-footer">
        {untimed.length > 0 ? (
          <div className="request-timeline-untimed">
            <span className="request-timeline-untimed-label">{t('Untimed:')}</span>
            {untimed.map((tc) => (
              <span
                key={tc.tool_use_id}
                className="request-timeline-untimed-pill"
                title={t('no duration reported')}
              >
                <span
                  className="request-timeline-swatch"
                  style={{ background: getToolColor(tc.tool_name).bg, opacity: 0.5 }}
                />
                {tc.tool_name}
              </span>
            ))}
          </div>
        ) : (
          <div />
        )}

        <div className="request-timeline-total">
          {t('Total')}: {formatLatency(displayTotal)}
        </div>
      </div>
    </div>
  )
}
