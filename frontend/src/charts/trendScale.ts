/** The usage trend plot is a 1000x200 user-space grid stretched to fill the
 *  panel, so one user unit is worth more pixels on x than on y. Circles turn
 *  into ellipses on narrow panels: this is the x factor that undoes it. */
export function trendDotScale(plotWidth: number, plotHeight = 220): number {
  if (!(plotWidth > 0)) return 1;
  return (plotHeight / 200) / (plotWidth / 1000);
}
