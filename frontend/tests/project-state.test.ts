// @vitest-environment jsdom
import { expect, it } from 'vitest'
import { detailSearch, projectSessionsUrl, projectStackMaximum, projectAverageLatency, costPerSession, projectBuckets } from '../src/projectState'
import { cacheHitRate, distinctColors, PALETTE } from '../src/utils'
it('encodes project and session deep links without losing existing parameters', () => {
  const params = new URLSearchParams({ project: 'repo / café', range: '30d', session: 'a&b' })
  expect(projectSessionsUrl('repo / café', '30d', 'a&b')).toBe(`/sessions?${params}`)
  expect(detailSearch(params, 'session', null).get('project')).toBe('repo / café')
  expect(params.get('session')).toBe('a&b')
  expect(detailSearch(params, 'project', 'new').get('session')).toBe('a&b')
})
it('scales each stacked bar by the largest combined project count', () => {
  expect(projectStackMaximum([])).toBe(1)
  expect(projectStackMaximum([{ key: 1, period: '', start: '', end: '', by_project: { a: 3, b: 4 } }, { key: 2, period: '', start: '', end: '', by_project: { a: 6, b: 2 } }])).toBe(8)
})
it('uses request-weighted latency, cost per session and disjoint cache-write inputs', () => {
  expect(projectAverageLatency(9000, 3)).toBe(3000)
  expect(projectAverageLatency(0, 0)).toBe(0)
  expect(costPerSession(6, 3)).toBe(2)
  expect(costPerSession(0, 0)).toBe(0)
  expect(cacheHitRate(40, 60, 20)).toBe(50)
})

it('trims an extra leading partial day to exactly 30 or 7 periods', () => {
  const buckets = Array.from({ length: 31 }, (_, key) => ({ key, period: '', start: '', end: '', by_project: { p: 1 } }))
  expect(projectBuckets(buckets, '30d')).toEqual(buckets.slice(1))
  expect(projectBuckets(buckets, '7d')).toHaveLength(7)
  expect(projectBuckets(buckets, 'all')).toBe(buckets)
})
it('keeps more than eight project colours distinct and stable using the existing helper', () => {
  const names = Array.from({ length: 12 }, (_, i) => `p${i}`)
  const base = new Map(names.map((name, index) => [name, PALETTE[index % PALETTE.length]]))
  const colors = () => distinctColors(names, name => base.get(name)!)
  expect(new Set(colors().values()).size).toBe(12)
  expect([...colors()]).toEqual([...colors()])
})
