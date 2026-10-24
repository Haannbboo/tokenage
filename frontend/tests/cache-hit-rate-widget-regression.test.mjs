import { describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const overview = readFileSync(join(root, 'src/pages/OverviewTab.tsx'), 'utf8')
const statTile = readFileSync(join(root, 'src/components/StatTile.tsx'), 'utf8')
const sparkline = readFileSync(join(root, 'src/Sparkline.tsx'), 'utf8')
const zh = readFileSync(join(root, 'src/i18n/zh.ts'), 'utf8')
const trendChart = readFileSync(join(root, 'src/charts/TrendChart.tsx'), 'utf8')
const modelBadge = readFileSync(join(root, 'src/model-badge.ts'), 'utf8')
const css = readFileSync(join(root, 'src/App.css'), 'utf8')

// Type stripping is on by default from node 22.18; older runtimes skip the
// runtime scale checks instead of failing the whole file.
const scaleModule = await import('../src/sparklineScale.ts').catch(() => null)
const gapsModule = await import('../src/periodGaps.ts').catch(() => null)
const trendTooltipModule = await import('../src/charts/trendTooltip.ts').catch(() => null)
const trendColorsModule = await import('../src/charts/trendColors.ts').catch(() => null)
// The i18n module reads the saved language on import.
globalThis.localStorage = globalThis.localStorage ?? { getItem: () => null, setItem: () => {} }
const labelsModule = await import('../src/periodLabels.ts').catch(() => null)

describe('Cache hit rate dashboard widget', () => {
  test('widget sits between Requests and Avg Speed', () => {
    const requests = overview.indexOf("t('Requests')")
    const cacheHit = overview.indexOf("t('Cache Hit Rate')")
    const avgSpeed = overview.indexOf("t('Avg Speed')")

    assert.ok(requests > -1, 'Requests tile exists')
    assert.ok(cacheHit > requests, 'Cache Hit Rate tile comes after Requests')
    assert.ok(avgSpeed > cacheHit, 'Cache Hit Rate tile comes before Avg Speed')
  })

  test('the cache hit rate series uses the shared helper, and an idle bucket reads as 0', () => {
    assert.match(
      overview,
      /value: cacheHitRate\(d\.cached_tokens, d\.prompt_tokens, d\.cache_creation_tokens\)/,
    )
    assert.match(overview, /v > 0 \? formatThroughput\(v\) : '0\.0 t\/s'/)
  })

  test('the hit rate trend line and cached total are not duplicated in the Token Usage tile', () => {
    const tokenUsageTile = overview.slice(overview.indexOf("t('Token Usage')"), overview.indexOf("t('Requests')"))
    assert.doesNotMatch(tokenUsageTile, /t\('Cached:'\)/)
  })

  test('the widget grid fits five tiles in one row on wide screens', () => {
    assert.match(css, /@media \(min-width: 1600px\) \{\s*\.widgets-grid \{\s*grid-template-columns: repeat\(5, minmax\(0, 1fr\)\)/)
  })

  test('Chinese translations exist', () => {
    for (const key of ['Cache Hit Rate', 'Cached:', 'of', 'tokens']) {
      assert.ok(zh.includes(`'${key}':`), `zh.ts should translate: ${key}`)
    }
  })

  test('no orphaned translation is left behind', () => {
    assert.doesNotMatch(zh, /'Hit\)'/)
  })
})

describe('Sparkline hover interaction', () => {
  test('the sparkline is controlled: it reports hover and never keeps its own index', () => {
    assert.match(sparkline, /onHover\?: \(index: number \| null\) => void/)
    assert.match(sparkline, /cursorIdx\?: number \| null/)
    assert.match(sparkline, /onPointerMove=/)
    assert.match(sparkline, /onPointerLeave=/)
    assert.match(sparkline, /onHover\?\.\(idx\)/)
    assert.match(sparkline, /onHover\?\.\(null\)/)
    assert.doesNotMatch(sparkline, /useState<number \| null>/)
  })

  test('a stale cursor index cannot crash the render', () => {
    assert.match(
      sparkline,
      /const cursor = cursorIdx !== null && cursorIdx >= 0 && cursorIdx < values\.length \? cursorIdx : null/,
    )
  })

  test('the stat tile swaps the number for the hovered value and shows its period', () => {
    assert.match(statTile, /const shown = !point \? format\(animatedValue\) : \(formatHover \?\? format\)\(point\.value\)/)
    assert.match(statTile, /const detail = point/)
  })

  test('every overview tile uses the hoverable stat tile', () => {
    const tiles = overview.match(/<StatTile/g) || []
    assert.equal(tiles.length, 5)
  })

  test('all five tiles share one cursor', () => {
    const handlers = overview.match(/onHover=\{setCursorIdx\}/g) || []
    assert.equal(handlers.length, 5)
    assert.match(overview, /const \[cursorIdx, setCursorIdx\] = useState<number \| null>\(null\)/)
  })
})

describe('Sparkline scale', { skip: !scaleModule }, () => {
  test('magnitudes keep the zero-based scale', () => {
    const { lo, hi, span } = scaleModule.sparklineScale([0, 50, 100], 'zero')
    assert.equal(lo, 0)
    assert.equal(hi, 100)
    assert.equal(span, 100)
  })

  test('a rate that stays high is zoomed into instead of drawn flat', () => {
    const { span } = scaleModule.sparklineScale([96.8, 97.1, 96.9, 97.2], 'relative')
    assert.ok(span < 5, `window should be zoomed, got ${span}`)
    assert.ok(span > 0.4, `window should not exaggerate, got ${span}`)
  })

  test('the zoomed window grows smoothly with the spread, with no cliff', () => {
    const narrow = scaleModule.sparklineScale([96.0, 97.9], 'relative')
    const wide = scaleModule.sparklineScale([95.9, 97.9], 'relative')
    assert.ok(Math.abs(narrow.span - wide.span) < 0.5, 'window must not jump at the floor')
  })

  test('a constant series is a flat line in the middle of the box', () => {
    const { lo, hi } = scaleModule.sparklineScale([50, 50, 50], 'relative')
    assert.ok(lo < 50 && hi > 50)
  })

  test('an empty bucket reads as 0 without blowing up the zoomed window', () => {
    const withZeros = scaleModule.sparklineScale([0, 96.8, 97.1, 0], 'relative')
    const without = scaleModule.sparklineScale([96.8, 97.1], 'relative')

    assert.equal(withZeros.span, without.span)
  })

  test('an all-zero series does not produce a broken scale', () => {
    const { span } = scaleModule.sparklineScale([0, 0])
    assert.ok(Number.isFinite(span) && span > 0)
  })
})

describe('Sparkline gap filling', { skip: !gapsModule }, () => {
  const row = (period, requests = 1) => ({ period, requests, total_tokens: 10, total_cost_usd: 0.5 })

  test('a day without usage is inserted as a zero bucket', () => {
    const filled = gapsModule.fillPeriodGaps([row('2026-10-20'), row('2026-10-22')], 'day')

    assert.deepEqual(filled.map(r => r.period), ['2026-10-20', '2026-10-21', '2026-10-22'])
    assert.equal(filled[1].requests, 0)
    assert.equal(filled[1].total_tokens, 0)
  })

  test('the rows that had usage are kept as they are', () => {
    const filled = gapsModule.fillPeriodGaps([row('2026-10-20'), row('2026-10-22')], 'day')

    assert.equal(filled[0].requests, 1)
    assert.equal(filled[2].requests, 1)
    assert.equal(filled[2].total_cost_usd, 0.5)
  })

  test('an hour without usage is inserted as a zero bucket', () => {
    const filled = gapsModule.fillPeriodGaps([row('2026-10-20 12:00'), row('2026-10-20 14:00')], 'hour')

    assert.deepEqual(filled.map(r => r.period), ['2026-10-20 12:00', '2026-10-20 13:00', '2026-10-20 14:00'])
    assert.equal(filled[1].requests, 0)
  })

  test('the series stays inside the range the data covers', () => {
    const filled = gapsModule.fillPeriodGaps([row('2026-10-20'), row('2026-10-22')], 'day')

    assert.equal(filled[0].period, '2026-10-20')
    assert.equal(filled[filled.length - 1].period, '2026-10-22')
  })

  test('a single row is left alone', () => {
    const data = [row('2026-10-20')]
    assert.equal(gapsModule.fillPeriodGaps(data, 'day').length, 1)
  })

  test('the overview uses the gap filled series for the tiles', () => {
    assert.match(overview, /fillPeriodGaps\(dailyUsage, granularity\)/)
    assert.match(overview, /const costPoints = filledUsage\.map/)
  })
})

describe('Period labels', { skip: !labelsModule }, () => {
  const now = Date.UTC(2026, 9, 22, 15, 0) // 2026-10-22 15:00

  test('a bucket of today or yesterday is marked relative', () => {
    assert.equal(labelsModule.describePeriod('2026-10-22', 'day', now, 'en-US').relative, 'today')
    assert.equal(labelsModule.describePeriod('2026-10-21', 'day', now, 'en-US').relative, 'yesterday')
  })

  test('an older day is named, with no relative word', () => {
    const label = labelsModule.describePeriod('2026-10-20', 'day', now, 'en-US')
    assert.equal(label.relative, undefined)
    assert.ok(/Oct 20/.test(label.day), label.day)
  })

  test('an hour bucket carries the clock, a day bucket does not', () => {
    const hour = labelsModule.describePeriod('2026-10-22 13:00', 'hour', now, 'en-US')
    assert.ok(hour.time && /1:00|13:00/.test(hour.time), hour.time)

    const day = labelsModule.describePeriod('2026-10-20', 'day', now, 'en-US')
    assert.equal(day.time, undefined)
  })

  test('an unknown period has no label', () => {
    assert.equal(labelsModule.describePeriod('not-a-period', 'day', now, 'en-US'), null)
  })

  test('the label reads as a sentence', () => {
    const today = labelsModule.describePeriod('2026-10-22 13:00', 'hour', now, 'en-US')
    assert.ok(labelsModule.periodLabelText(today).startsWith('Today '), today.time)

    const older = labelsModule.describePeriod('2026-10-20', 'day', now, 'en-US')
    assert.ok(/Oct 20/.test(labelsModule.periodLabelText(older)))
  })
})

describe('Usage trend chart', () => {
  test('the throughput view is gone', () => {
    assert.doesNotMatch(trendChart, /'throughput'/)
    assert.doesNotMatch(trendChart, /setMetric\('throughput'\)/)
    assert.doesNotMatch(trendChart, /maxThroughput/)
    assert.doesNotMatch(zh, /'Throughput':/)
  })

  test('the hovered bucket gets an animated band and a crosshair', () => {
    assert.match(trendChart, /fill=\{`url\(#\$\{hoverBandId\}\)`\}/)
    assert.match(trendChart, /stroke=\{`url\(#\$\{crosshairId\}\)`\}/)
    assert.match(trendChart, /transition: 'transform 160ms cubic-bezier\(0\.22, 1, 0\.36, 1\)'/)
  })

  test('hovering moves the tooltip without re-rendering the chart', () => {
    assert.match(trendChart, /tooltip\.style\.transform/)
    assert.match(trendChart, /onPointerMove=\{handlePointer\}/)
    assert.doesNotMatch(trendChart, /onMouseMove/)
    assert.match(trendChart, /animation: 'trend-tooltip-in/)
    assert.match(trendChart, /className="trend-hover-pulse"/)
    assert.match(css, /@keyframes trend-hover-pulse/)
  })

  test('the hovered point stays marked even when the dots are hidden', () => {
    assert.match(trendChart, /The hovered point stays marked even when the dots are off/)
  })

  test('without a period count the plot spans the data instead of a fixed window', () => {
    assert.match(trendChart, /periodCount\?: number/)
    assert.match(trendChart, /periodCount === undefined\s*\? fillPeriodGaps\(data, granularity\)/)
  })

  test('the tooltip follows the cursor near the edges without leaving the viewport', () => {
    assert.match(trendChart, /describePeriod\(hoveredData\.period, granularity, nowWallMs/)
    assert.doesNotMatch(trendChart, /Math\.min\(Math\.max\(e\.clientX - rect\.left, half\), rect\.width - half\)/)
    assert.match(trendChart, /const overflow = Math\.min\(/)
    assert.match(trendChart, /tooltipWidthRef\.current = tooltip\.offsetWidth/)
    assert.match(trendChart, /Math\.max\(0, rect\.left - 8\)/)
    assert.match(trendChart, /Math\.max\(0, window\.innerWidth - rect\.right - 8\)/)
    assert.match(trendChart, /maxWidth: 'min\(520px, calc\(100% - 16px\)\)'/)
  })

  test('the hover highlight uses the active metric colour', () => {
    assert.match(trendChart, /const hoverColor = metric === 'cost' \? 'var\(--color-blue\)' : 'var\(--color-yellow\)'/)
    assert.match(trendChart, /stopColor=\{hoverColor\}/)
    assert.match(trendChart, /stroke=\{hoverColor\} strokeWidth=\{6\}/)
  })

  test('the model rows are ordered and aligned by the active metric', () => {
    assert.match(trendChart, /sortTrendRows\(bucketSegments\(hoveredData\.period\), metric\)/)
    assert.match(trendChart, /gridTemplateColumns: 'minmax\(0, 1fr\) auto auto'/)
    assert.match(trendChart, /textAlign: 'right'/)
  })

  test('the overview leaves the period count open for all time', () => {
    assert.match(overview, /periodCount=\{rangePeriodCount\(dateRange\)\}/)
  })
})

describe('Trend tooltip helpers', { skip: !trendTooltipModule }, () => {
  test('orders model rows by the active metric', () => {
    const { sortTrendRows } = trendTooltipModule
    const rows = [
      { model: 'a', tokens: 10, cost: 1 },
      { model: 'b', tokens: 30, cost: 0.5 },
      { model: 'c', tokens: 20, cost: 2 },
    ]
    assert.deepEqual(sortTrendRows(rows, 'tokens').map(r => r.model), ['b', 'c', 'a'])
    assert.deepEqual(sortTrendRows(rows, 'cost').map(r => r.model), ['c', 'a', 'b'])
  })

  test('breaks ties with the other metric', () => {
    const { sortTrendRows } = trendTooltipModule
    const rows = [
      { model: 'a', tokens: 10, cost: 1 },
      { model: 'b', tokens: 20, cost: 1 },
    ]
    assert.deepEqual(sortTrendRows(rows, 'cost').map(r => r.model), ['b', 'a'])
    assert.deepEqual(sortTrendRows(rows, 'tokens').map(r => r.model), ['b', 'a'])
  })

  test('centres on the cursor in the middle', () => {
    const { trendTooltipX } = trendTooltipModule
    assert.equal(trendTooltipX(500, 1000, 520, 192), 500)
  })

  test('keeps moving near the left and right edges instead of freezing', () => {
    const { trendTooltipX } = trendTooltipModule
    assert.ok(trendTooltipX(0, 1000, 520, 192) < trendTooltipX(60, 1000, 520, 192))
    assert.ok(trendTooltipX(1000, 1000, 520, 192) > trendTooltipX(940, 1000, 520, 192))
  })

  test('never drifts past the available overflow', () => {
    const { trendTooltipX } = trendTooltipModule
    assert.equal(trendTooltipX(0, 1000, 520, 0), 268)
    assert.equal(trendTooltipX(0, 1000, 520, 192), 76)
    assert.equal(trendTooltipX(1000, 1000, 520, 0), 732)
    assert.equal(trendTooltipX(1000, 1000, 520, 192), 924)
  })

  test('handles a zero-width chart', () => {
    const { trendTooltipX } = trendTooltipModule
    assert.equal(trendTooltipX(10, 0, 520, 0), 0)
  })
})

describe('Trend colours', { skip: !trendColorsModule }, () => {
  test('pins Claude variants and MiniMax to distinct brand shades', () => {
    const { trendBrandColor } = trendColorsModule
    assert.equal(trendBrandColor('claude-opus-5-5', '#cc7c5e'), '#9a5b3c')
    assert.equal(trendBrandColor('claude-sonnet-5-5', '#cc7c5e'), '#c98a63')
    assert.equal(trendBrandColor('MiniMax-M2', '#ec6b53'), '#ef4444')
    assert.equal(trendBrandColor('claude-haiku', '#cc7c5e'), '#cc7c5e')
  })

  test('boosts the chroma of the brand colours', () => {
    const { vibrantColor } = trendColorsModule
    const chroma = (hex) => {
      const n = parseInt(hex.slice(1), 16)
      const r = (n >> 16) & 255
      const g = (n >> 8) & 255
      const b = n & 255
      return Math.max(r, g, b) - Math.min(r, g, b)
    }
    for (const base of ['#cc7c5e', '#475569', '#dcc496', '#4d7cff']) {
      assert.ok(chroma(vibrantColor(base)) > chroma(base))
    }
  })
})

describe('Usage trend by model', () => {
  test('tokens and cost are both broken down by model', () => {
    assert.match(trendChart, /useState<'tokens' \| 'cost'>/)
    assert.doesNotMatch(trendChart, /tokenSplit|showModels/)
    assert.match(trendChart, /const segment = metric === 'cost' \? m\.cost : m\.tokens/)
  })

  test('the token-type and input/output-cost stacking is gone', () => {
    assert.doesNotMatch(trendChart, /grad-tokens|grad-cost/)
    assert.doesNotMatch(trendChart, /'Input Cost'|'Output Cost'|'Cached:'|'Input:'|'Output:'/)
  })

  test('the model split comes from the per-model endpoint with the right granularity', () => {
    assert.match(trendChart, /new URL\('\/usage\/daily-by-dimension', window\.location\.origin\)/)
    assert.match(trendChart, /url\.searchParams\.set\('dimension', 'model'\)/)
    assert.match(trendChart, /url\.searchParams\.set\('granularity', granularity\)/)
  })

  test('the bars are stacked by model and coloured per model', () => {
    assert.match(trendChart, /const stack = bucketSegments\(d\.period\)\s*\.filter/)
    assert.match(trendChart, /fill=\{m\.model \? modelColor\(m\.model\) : 'var\(--text-muted\)'\}/)
  })

  test('the model order and colours are stable across the metric tabs', () => {
    assert.match(trendChart, /\.sort\(\(a, b\) => b\[1\]\.tokens - a\[1\]\.tokens\)/)
    assert.match(trendChart, /modelTotals\.slice\(0, MODEL_LEGEND_MAX\)/)
    assert.match(trendChart, /modelColors\.get\(model\)/)
    assert.doesNotMatch(trendChart, /MODEL_CHART_COLORS/)
  })

  test('the trend colours use the existing brand palette', () => {
    assert.match(trendChart, /import \{ getModelColor, getModelTextColor \} from '\.\.\/model-badge'/)
    assert.match(trendChart, /vibrantColor\(trendBrandColor\(model, getModelColor\(model, theme\)\)\)/)
    assert.match(trendChart, /vibrantColor\(getModelTextColor\(model, theme\)\)/)
    assert.match(modelBadge, /if \(m\.includes\('gpt'\) \|\| m\.includes\('codex'\)\)/)
    assert.match(modelBadge, /if \(m\.includes\('mimo'\)\)/)
  })

  test('the stack caps round at the real top and bottom of each bar', () => {
    assert.match(trendChart, /const isTop = mi === stack\.length - 1/)
    assert.match(trendChart, /const isBottom = mi === 0/)
  })

  test('the legend sits below the chart, after the x-axis labels', () => {
    assert.match(trendChart, /borderTop: '1px solid var\(--chart-footer-border\)'[\s\S]*legendModels\.map/)
  })

  test('the stacks keep the legend order and pool the rest into other', () => {
    assert.match(trendChart, /const segments = legendModels\.flatMap\(m => \{/)
    assert.match(trendChart, /if \(other\.tokens > 0 \|\| other\.cost > 0\) segments\.push\(other\)/)
  })

  test('the legend lists the models, the rest as other', () => {
    assert.match(trendChart, /const legendModels = modelTotals\.slice\(0, MODEL_LEGEND_MAX\)/)
    assert.match(trendChart, /t\('Other'\)/)
  })

  test('the overview passes the dashboard filters to the trend chart', () => {
    assert.match(overview, /filterParams=\{dashboardFilterParams\}/)
  })

  test('tooltip prices use three fixed decimals with tabular figures', () => {
    assert.match(trendChart, /formatCostFixed\(m\.cost, 3\)/)
    assert.match(trendChart, /formatCostFixed\(hoveredData\.total_cost_usd, 3\)/)
    assert.match(trendChart, /fontVariantNumeric: 'tabular-nums'/)
  })
})
