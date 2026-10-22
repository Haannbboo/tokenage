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

export function sparklineScale(values: (number | null)[], mode: ScaleMode = 'zero'): SparklineScale {
  const nums = values.filter((v): v is number => v !== null)
  if (nums.length === 0) return { lo: 0, hi: 1, span: 1 }

  const dataMin = Math.min(...nums)
  const dataMax = Math.max(...nums)
  const dataRange = dataMax - dataMin

  if (mode === 'zero') {
    const lo = Math.min(0, dataMin)
    const hi = dataMax
    return { lo, hi, span: hi - lo || 1 }
  }

  // Centred on the data, so the window grows smoothly with the spread: no
  // cliff where a series just past the floor suddenly draws flat.
  const span = Math.max(dataRange, Math.abs(dataMax) * ZOOM_FLOOR)
  const mid = (dataMax + dataMin) / 2
  const lo = mid - span / 2
  const hi = mid + span / 2
  return { lo, hi, span: hi - lo || 1 }
}
