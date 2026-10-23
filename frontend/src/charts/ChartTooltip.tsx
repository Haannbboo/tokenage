import { forwardRef } from 'react'

export const ChartTooltip = forwardRef<HTMLDivElement, {
  left: string
  style?: React.CSSProperties
  children: React.ReactNode
}>(function ChartTooltip({ left, style, children }, ref) {
  return (
    <div ref={ref} style={{
      position: 'absolute',
      top: '-10px',
      left,
      transform: 'translateX(-50%)',
      backgroundColor: 'var(--glass-bg)',
      color: 'var(--text-primary)',
      padding: '12px',
      borderRadius: 'var(--radius-md)',
      fontSize: '12px',
      zIndex: 100,
      pointerEvents: 'none',
      boxShadow: 'var(--shadow-floating)',
      minWidth: '200px',
      border: '1px solid var(--glass-border)',
      backdropFilter: 'var(--glass-blur)',
      WebkitBackdropFilter: 'var(--glass-blur)',
      ...style,
    }}>
      {children}
    </div>
  )
})

export function TooltipRow({
  label,
  labelColor,
  children,
}: {
  label: string
  labelColor?: string
  children: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', marginBottom: '4px' }}>
      <span style={{ color: labelColor ?? 'var(--text-muted)' }}>{label}</span>
      {children}
    </div>
  )
}

export function TooltipDivider() {
  return (
    <div style={{
      marginTop: '8px',
      paddingTop: '8px',
      borderTop: '1px solid rgba(255, 255, 255, 0.2)',
    }} />
  )
}
