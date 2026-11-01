/**
 * Window a sparkline maps its values into.
 *
 * 'zero' keeps the natural 0-based scale: right for magnitudes (cost, tokens,
 * requests) where the line must not pretend a small change is a big one.
 *
 * 'relative' zooms into the data instead, centred on it and at least
 * ZOOM_FLOOR of the series max wide. A rate that stays near 97% is a dead
 * flat line on a 0-based scale; this shows its shape. The floor keeps the
 * zoom bounded, so a wiggle smaller than ZOOM_FLOOR stays a small wiggle
 * instead of being stretched across the whole box.
 */
export type ScaleMode = 'zero' | 'relative'

export const ZOOM_FLOOR = 0.02

export type SparklineScale = {
  lo: number
  hi: number
  span: number
}

export function sparklineScale(values: number[], mode: ScaleMode = 'zero'): SparklineScale {
  if (values.length === 0) return { lo: 0, hi: 1, span: 1 }

  const dataMin = Math.min(...values)
  const dataMax = Math.max(...values)

  if (mode === 'zero') {
    const lo = Math.min(0, dataMin)
    const hi = dataMax
    return { lo, hi, span: hi - lo || 1 }
  }

  // A zero in a relative series is a bucket without activity, not a real zero,
  // so it must not blow the window open (that would flatten the whole trend).
  const active = values.filter(v => v !== 0)
  const activeMin = active.length ? Math.min(...active) : dataMin
  const activeMax = active.length ? Math.max(...active) : dataMax

  // Centred on the data, so the window grows smoothly with the spread: no
  // cliff where a series just past the floor suddenly draws flat.
  const span = Math.max(activeMax - activeMin, Math.abs(activeMax) * ZOOM_FLOOR)
  const mid = (activeMax + activeMin) / 2
  const lo = mid - span / 2
  const hi = mid + span / 2
  return { lo, hi, span: hi - lo || 1 }
}
