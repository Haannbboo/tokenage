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
const css = readFileSync(join(root, 'src/App.css'), 'utf8')

// Type stripping is on by default from node 22.18; older runtimes skip the
// runtime scale checks instead of failing the whole file.
const scaleModule = await import('../src/sparklineScale.ts').catch(() => null)

describe('Cache hit rate dashboard widget', () => {
  test('widget sits between Requests and Avg Speed', () => {
    const requests = overview.indexOf("t('Requests')")
    const cacheHit = overview.indexOf("t('Cache Hit Rate')")
    const avgSpeed = overview.indexOf("t('Avg Speed')")

    assert.ok(requests > -1, 'Requests tile exists')
    assert.ok(cacheHit > requests, 'Cache Hit Rate tile comes after Requests')
    assert.ok(avgSpeed > cacheHit, 'Cache Hit Rate tile comes before Avg Speed')
  })

  test('the cache hit rate series uses the shared helper and no data is a gap', () => {
    assert.match(
      overview,
      /value: cacheHitRate\(d\.cached_tokens, d\.prompt_tokens, d\.cache_creation_tokens\)/,
    )
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

  test('a period with no data leaves a gap instead of a zero', () => {
    assert.match(sparkline, /v === null \? null : \{ x:/)
    assert.match(sparkline, /if \(run\.length > 1\) segments\.push\(run\)/)
  })

  test('the stat tile swaps the number for the hovered value and shows its period', () => {
    assert.match(statTile, /point\.value === null\s*\? '—'/)
    assert.match(statTile, /\{point \? point\.period : children\}/)
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

  test('periods with no data do not drag the window down', () => {
    const withGap = scaleModule.sparklineScale([96.8, null, 97.1], 'relative')
    const without = scaleModule.sparklineScale([96.8, 97.1], 'relative')
    assert.equal(withGap.span, without.span)
  })

  test('an all-zero series does not produce a broken scale', () => {
    const { span } = scaleModule.sparklineScale([0, 0])
    assert.ok(Number.isFinite(span) && span > 0)
  })
})
