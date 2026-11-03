import { forwardRef } from 'react'

/** The one chart tooltip look (`.chart-tip`); callers position it. */
export const ChartTooltip = forwardRef<HTMLDivElement, {
  left?: string
  style?: React.CSSProperties
  className?: string
  id?: string
  role?: string
  children: React.ReactNode
}>(function ChartTooltip({ left, style, className, id, role, children }, ref) {
  return (
    <div
      ref={ref}
      id={id}
      role={role}
      className={`chart-tip${className ? ` ${className}` : ''}`}
      style={left === undefined ? style : { position: 'absolute', top: '-10px', left, transform: 'translateX(-50%)', minWidth: '200px', ...style }}
    >
      {children}
    </div>
  )
})

export function TooltipTitle({ children }: { children: React.ReactNode }) {
  return <div className="chart-tip-title">{children}</div>
}

export function TooltipRow({
  label,
  labelColor,
  swatch,
  total = false,
  children,
}: {
  label: string
  labelColor?: string
  /** A colour that carries meaning, shown as a small square before the label. */
  swatch?: string
  total?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`chart-tip-row${total ? ' chart-tip-total' : ''}`}>
      <span className="chart-tip-label" style={labelColor ? { color: labelColor } : undefined}>
        {swatch && <i style={{ background: swatch }} />}
        {label}
      </span>
      <span className="chart-tip-value">{children}</span>
    </div>
  )
}

export function TooltipDivider() {
  return <div className="chart-tip-divider" />
}
