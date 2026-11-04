/** The usage trend plot is a 1000x200 user-space grid stretched to fill the
 *  panel, so one user unit is worth more pixels on x than on y. Circles turn
 *  into ellipses on narrow panels: this is the x factor that undoes it. */
export function trendDotScale(plotWidth: number, plotHeight = 220): number {
  if (!(plotWidth > 0)) return 1;
  return (plotHeight / 200) / (plotWidth / 1000);
}

/** Indices of the x-axis labels to show: evenly stepped so `labelPx` (one
 *  10px label plus its gap) fits `width`, at most 12 in all, always the first
 *  and the last. The last replaces the final stepped label, which always sits
 *  closer than a step to it, so the count stays within the budget. */
export function trendLabelIndices(count: number, width: number, labelPx = 44): number[] {
  if (count <= 0) return []
  const fit = width > 0 ? Math.max(2, Math.floor(width / labelPx)) : 12
  const step = Math.ceil(count / Math.min(12, fit))
  const shown: number[] = []
  for (let i = 0; i < count; i += step) shown.push(i)
  const last = count - 1
  if (shown[shown.length - 1] !== last) {
    if (shown.length > 1) shown.pop()
    shown.push(last)
  }
  return shown
}
