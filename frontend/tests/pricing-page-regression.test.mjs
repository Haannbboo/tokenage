import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = join(dirname(fileURLToPath(import.meta.url)), '..')
const appContextSource = readFileSync(join(here, 'src', 'contexts', 'AppContext.tsx'), 'utf-8')
const hookSource = readFileSync(join(here, 'src', 'hooks', 'usePricingData.ts'), 'utf-8')
const pageSource = readFileSync(join(here, 'src', 'pages', 'PricingPage.tsx'), 'utf-8')

test('app context initial load does not write pricing data', () => {
  assert.doesNotMatch(appContextSource, /fetch\('\/pricing'/)
  assert.doesNotMatch(appContextSource, /setPricingData\(await pricingResp\.json\(\)\)/)
})

test('pricing page fetches model-only pricing', () => {
  assert.match(hookSource, /fetch\('\/pricing', \{/)
  assert.doesNotMatch(hookSource, /provider/i)
  assert.doesNotMatch(pageSource, /multiplier|\.scope|effective_/i)
})

test('pricing page keeps slash-bearing model ids visible', () => {
  assert.doesNotMatch(hookSource, /name\.includes\('\/'\)/)
})

test('provider pricing view shows and edits cache write pricing', () => {
  assert.match(pageSource, /Cache Write \(per 1M\)/)
  assert.match(pageSource, /inputProps\(model, 'cacheWrite'\)/)
  assert.match(pageSource, /model\.cache_write/)
})

test('provider pricing view uses resolved prices for missing yaml-field placeholders', () => {
  assert.match(pageSource, /const price = modelPrice\(model, field\)/)
  assert.match(pageSource, /price !== undefined && price !== null \? String\(price\) : '—'/)
  assert.doesNotMatch(pageSource, /String\(activeCost\[field\]\) : "0\.000"/)
})

test('pricing page renders tiers and time-of-day windows', () => {
  assert.match(pageSource, /Token tiers/)
  assert.match(pageSource, /Time-of-day rates/)
  assert.match(pageSource, /model\.time_rates/)
})

test('pricing empty state spans all pricing columns', () => {
  assert.match(pageSource, /colSpan=\{8\}/)
})

test('pricing page lists Custom overrides first and defaults to them', () => {
  assert.match(pageSource, /user: \{[^}]*label: 'Custom' \}/)
  assert.match(pageSource, /yaml: \{[^}]*label: 'Server' \}/)
  assert.match(pageSource, /useState<'all' \| 'user' \| 'yaml' \| 'litellm' \| 'openrouter'>\('user'\)/)
})

test('pricing page offers reprice, add model and reset for custom rows', () => {
  assert.match(pageSource, /checked=\{repricePast\}/)
  assert.match(pageSource, /addModel\(newModelName\)/)
  assert.match(pageSource, /model\.source === 'user' && \(/)
  assert.match(pageSource, /handleResetPricing\(model\.name\)/)
})
