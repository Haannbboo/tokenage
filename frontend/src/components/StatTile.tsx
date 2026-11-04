import type { ReactNode } from 'react'
import { Sparkline } from '../Sparkline'
import type { ScaleMode } from '../sparklineScale'
import { t } from '../i18n/index.ts'

export type StatPoint = {
  value: number
  period: string
  active?: boolean
}

type StatTileProps = {
  label: string
  color: string
  points: StatPoint[]
  /** Shown when nothing is hovered. */
  animatedValue: number
  format: (value: number) => ReactNode
  /** Overrides `format` for the hovered value, when the unit differs. */
  formatHover?: (value: number) => ReactNode
  scale?: ScaleMode
  cursorIdx: number | null
  /** Readable label for the hovered period, shared by the whole row. */
  hoverLabel?: string | null
  onHover: (index: number | null) => void
  children?: ReactNode
  /** The value belongs to another view that is still loading: show a placeholder. */
  pending?: boolean
}

export function StatTile({
  label, color, points, animatedValue, format, formatHover,
  scale = 'zero', cursorIdx, hoverLabel, onHover, children, pending = false,
}: StatTileProps) {
  if (pending) {
    return (
      <div className="widget" aria-busy="true">
        <div className="stat-label">{label}</div>
        <div className="stat-value">—</div>
        <div className="stat-detail">—</div>
        {/* Holds the sparkline's height so nothing moves when the data lands. */}
        <div style={{ height: 24 }} />
      </div>
    )
  }
  const point = cursorIdx === null ? undefined : points[cursorIdx]
  const shown = !point ? format(animatedValue) : (formatHover ?? format)(point.value)

  // Idle buckets read as 0, so they are left out of the extremes.
  const active = points.filter(p => p.active ?? p.value !== 0).map(p => p.value)
  const highest = active.length ? Math.max(...active) : null
  const lowest = active.length ? Math.min(...active) : null
  const marker = point && (point.active ?? point.value !== 0) && highest !== null && lowest !== null
    ? (point.value === highest ? t('peak') : point.value === lowest ? t('lowest') : null)
    : null

  const detail = point
    ? [hoverLabel ?? point.period, marker].filter(Boolean).join(' · ')
    : children

  return (
    <div className="widget">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{shown}</div>
      {/* Hovering swaps the detail line for the period of the hovered point. */}
      <div className="stat-detail">{detail}</div>
      {points.length < 2 ? <div style={{ height: 24 }} /> : <Sparkline
        values={points.map(p => p.value)}
        color={color}
        height={24}
        mode={scale}
        cursorIdx={cursorIdx}
        onHover={onHover}
      />}
    </div>
  )
}
