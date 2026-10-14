import { describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const dashboard = readFileSync(join(root, 'src/pages/SessionsTab.tsx'), 'utf8')
const types = readFileSync(join(root, 'src/types.ts'), 'utf8')
const css = readFileSync(join(root, 'src/App.css'), 'utf8')
const zh = readFileSync(join(root, 'src/i18n/zh.ts'), 'utf8')

describe('Daily effectiveness report UI', () => {
  test('types match the sessions daily effectiveness endpoint shape', () => {
    assert.match(types, /export type DailyEffectivenessReport/)
    assert.match(types, /session_count: number/)
    assert.match(types, /evaluated_count: number/)
    assert.match(types, /model_takeaways: string\[\]/)
    assert.match(types, /export type DailyEffectivenessGroup/)
  })

  test('dashboard fetches the sessions daily effectiveness endpoint', () => {
    assert.match(dashboard, /new URL\('\/sessions\/daily-effectiveness'/)
    assert.match(dashboard, /searchParams\.set\('date', todayDateKey\)/)
    assert.match(dashboard, /setDailyEffectivenessReport/)
  })

  test('dashboard renders a today work panel with automatic loading and bullets', () => {
    assert.match(dashboard, /daily-effectiveness-panel/)
    assert.match(dashboard, /t\('Today’s AI Work'\)/)
    assert.match(dashboard, /fetchDailyEffectivenessReport, refreshTrigger/)
    assert.doesNotMatch(dashboard, /t\('Refresh report'\)/)
    assert.match(dashboard, /dailyEffectivenessReport\.highlights/)
    assert.match(dashboard, /dailyEffectivenessReport\.needs_attention/)
    assert.match(dashboard, /dailyEffectivenessReport\.model_takeaways/)
  })

  test('panel styles exist', () => {
    assert.match(css, /\.daily-effectiveness-panel/)
    assert.match(css, /\.daily-effectiveness-metrics/)
    assert.match(css, /\.daily-effectiveness-list/)
  })

  test('Chinese translations exist', () => {
    const expectedKeys = [
      'Today’s AI Work',
      'Evaluated',
      'Classified',
      'Needs attention',
      'No daily effectiveness report yet.',
    ]

    for (const key of expectedKeys) {
      assert.ok(zh.includes(`'${key}':`), `zh.ts should translate: ${key}`)
    }
  })
})
