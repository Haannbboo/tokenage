import { PALETTE } from '../utils'

/** The colour of a model with no series of its own. */
export const TREND_NEUTRAL = 'var(--text-muted)'

/** Colours for the series drawn in the chart, in legend order (biggest first): the shared palette, in turn. */
export function trendSeriesColors(series: { key: string }[]): Map<string, string> {
  return new Map(series.map((s, i) => [s.key, PALETTE[i % PALETTE.length]]))
}
