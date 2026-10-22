import type { ReactNode } from 'react'
import { Sparkline } from '../Sparkline'
import type { ScaleMode } from '../sparklineScale'

export type StatPoint = {
  /** null marks a period with no data. */
  value: number | null
  period: string
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
  onHover: (index: number | null) => void
  children?: ReactNode
}

export function StatTile({
  label, icon, iconClass, color, points, animatedValue, format, formatHover,
  scale = 'zero', cursorIdx, onHover, children,
}: StatTileProps) {
  const point = cursorIdx === null ? undefined : points[cursorIdx]
  const shown = !point
    ? format(animatedValue)
    : point.value === null
      ? '—'
      : (formatHover ?? format)(point.value)

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
          {point ? point.period : children}
        </div>
      </div>
    </div>
  );
}
