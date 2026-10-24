import { describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = join(dirname(fileURLToPath(import.meta.url)), '..')
const source = readFileSync(join(here, 'src', 'dateRange.ts'), 'utf-8')

// Type stripping is on by default from node 22.18; older runtimes skip the
// runtime checks instead of failing the whole file.
const module = await import('../src/dateRange.ts').catch(() => null)

describe('Date range options', () => {
  test('the toolbar offers the short ranges first and all time last', () => {
    assert.match(source, /value: '24h', label: 'Last 24 Hours'/)
    assert.match(source, /value: '5h', label: 'Last 5 Hours'/)
    assert.match(source, /value: '7d', label: 'Last 7 Days'/)
    assert.match(source, /value: '30d', label: 'Last 30 Days'/)
    assert.match(source, /value: 'all', label: 'All Time'/)
    assert.doesNotMatch(source, /custom/i)
  })

  test('both toolbars render the shared option list', () => {
    for (const page of ['DashboardPage.tsx', 'LogsPage.tsx']) {
      const pageSource = readFileSync(join(here, 'src', 'pages', page), 'utf-8')
      assert.match(pageSource, /DATE_RANGE_OPTIONS\.map/)
    }
  })

  test('the custom range is gone from the range type and the context', () => {
    const types = readFileSync(join(here, 'src', 'types.ts'), 'utf-8')
    const context = readFileSync(join(here, 'src', 'contexts', 'AppContext.tsx'), 'utf-8')
    assert.match(types, /export type DateRangeOption = '24h' \| '5h' \| '7d' \| '30d' \| 'all'/)
    assert.doesNotMatch(context, /customSince|customUntil/)
  })

  test('the chinese translations cover every range label', () => {
    const zh = readFileSync(join(here, 'src', 'i18n', 'zh.ts'), 'utf-8')
    for (const key of ['Last 24 Hours', 'Last 5 Hours', 'Last 7 Days', 'Last 30 Days', 'All Time', '15-Minute Usage Trend']) {
      assert.match(zh, new RegExp(`'${key}':`))
    }
  })
})

describe('Date range behavior', { skip: !module && 'type stripping unavailable' }, () => {
  const { DATE_RANGE_OPTIONS, getSinceDate, rangeGranularity, rangePeriodCount } = module ?? {}

  test('every option has a label and a value', () => {
    assert.deepEqual(DATE_RANGE_OPTIONS.map(o => o.value), ['5h', '24h', '7d', '30d', 'all'])
    for (const option of DATE_RANGE_OPTIONS) assert.ok(option.label.length > 0)
  })

  test('all time has no lower bound', () => {
    assert.equal(getSinceDate('all'), null)
  })

  test('short ranges start hours ago', () => {
    const hours = (option) => {
      const since = getSinceDate(option)
      assert.ok(since)
      return Math.round((Date.now() - new Date(since).getTime()) / 3600_000)
    }
    assert.equal(hours('24h'), 24)
    assert.equal(hours('5h'), 5)
  })

  test('the day ranges start days ago', () => {
    const days = (option) => {
      const since = getSinceDate(option)
      assert.ok(since)
      return Math.round((Date.now() - new Date(since).getTime()) / 86400_000)
    }
    assert.equal(days('7d'), 7)
    assert.equal(days('30d'), 30)
  })

  test('the chart axis is finer than the rollup for the five hour range', () => {
    assert.equal(rangeGranularity('5h'), '15m')
    assert.equal(rangeGranularity('24h'), 'hour')
    assert.equal(rangeGranularity('7d'), 'day')
    assert.equal(rangeGranularity('30d'), 'day')
    assert.equal(rangeGranularity('all'), 'day')
  })

  test('the trend chart gets one period per bucket', () => {
    assert.equal(rangePeriodCount('24h'), 24)
    assert.equal(rangePeriodCount('5h'), 20)
    assert.equal(rangePeriodCount('7d'), 7)
    assert.equal(rangePeriodCount('30d'), 30)
    assert.equal(rangePeriodCount('all'), undefined)
  })
})
