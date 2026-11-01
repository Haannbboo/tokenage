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
  icon: string
  iconClass: string
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
}

export function StatTile({
  label, icon, iconClass, color, points, animatedValue, format, formatHover,
  scale = 'zero', cursorIdx, hoverLabel, onHover, children,
}: StatTileProps) {
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
      <div className="widget-body" style={{ flexDirection: 'column', alignItems: 'stretch', justifyContent: 'flex-start' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div className={`icon-box ${iconClass}`}>{icon}</div>
            <div>
              <div className="stat-label">{label}</div>
              <div className="stat-value">{shown}</div>
            </div>
          </div>
          <div style={{ width: '100px' }}>
            <Sparkline
              values={points.map(p => p.value)}
              color={color}
              mode={scale}
              cursorIdx={cursorIdx}
              onHover={onHover}
            />
          </div>
        </div>
        {/* Hovering swaps the detail line for the period of the hovered point. */}
        <div className="stat-label" style={{ marginBottom: 0 }}>
          {detail}
        </div>
      </div>
    </div>
  );
}
