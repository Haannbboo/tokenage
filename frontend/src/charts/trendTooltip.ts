/** Keep the tooltip under the cursor in the middle. Near an edge it keeps
 *  moving at a slower rate and drifts up to `availableOverflow` past the plot
 *  instead of freezing a full tooltip-width away from the pointer. */
export function trendTooltipX(
  cursorX: number,
  chartWidth: number,
  tooltipWidth: number,
  availableOverflow: number,
) {
  const half = Math.min(tooltipWidth / 2 + 8, chartWidth / 2);
  if (half <= 0) return 0;
  const overflow = Math.max(0, Math.min(half, availableOverflow));
  if (cursorX < half) {
    return half - overflow + overflow * (cursorX / half);
  }
  if (cursorX > chartWidth - half) {
    return chartWidth - half + overflow - overflow * ((chartWidth - cursorX) / half);
  }
  return cursorX;
}

export function sortTrendRows<T extends { model: string; tokens: number; cost: number }>(
  rows: T[],
  metric: 'tokens' | 'cost',
): T[] {
  return rows.slice().sort((a, b) => {
    const primary = metric === 'cost' ? b.cost - a.cost : b.tokens - a.tokens;
    if (primary !== 0) return primary;
    const secondary = metric === 'cost' ? b.tokens - a.tokens : b.cost - a.cost;
    return secondary !== 0 ? secondary : a.model.localeCompare(b.model);
  });
}
