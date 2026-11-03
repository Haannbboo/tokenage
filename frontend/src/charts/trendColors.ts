import { distinctColors, modelColor } from '../utils'

/** The colour of a model with no series of its own. */
export const TREND_NEUTRAL = 'var(--text-muted)'

/** Colours for the series drawn in the chart, in legend order: each model's dimmed identity colour, shaded apart when two share a hue. */
export function trendSeriesColors(series: { key: string; model: string }[]): Map<string, string> {
  const modelOf = new Map(series.map(s => [s.key, s.model]))
  const colors = distinctColors(series.map(s => s.key), key => modelColor(modelOf.get(key) ?? key))
  // --trend-saturation (index.css) calms the many-coloured stack without touching the shared hues.
  return new Map([...colors].map(([key, color]) => [key, `oklch(from ${color} l calc(c * var(--trend-saturation)) h)`]))
}
