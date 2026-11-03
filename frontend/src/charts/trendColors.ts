import { distinctColors, modelColor } from '../utils'

/** The colour of a model with no series of its own. */
export const TREND_NEUTRAL = 'var(--text-muted)'

/** Colours for the series drawn in the chart, in legend order: each model's dimmed identity colour, shaded apart when two share a hue. */
export function trendSeriesColors(series: { key: string; model: string }[]): Map<string, string> {
  const modelOf = new Map(series.map(s => [s.key, s.model]))
  return distinctColors(series.map(s => s.key), key => modelColor(modelOf.get(key) ?? key))
}
