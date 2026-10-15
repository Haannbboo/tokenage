import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = join(dirname(fileURLToPath(import.meta.url)), '..')
const pricingHook = readFileSync(join(here, 'src', 'hooks', 'usePricingData.ts'), 'utf-8')

function extractCallback(source, name) {
  const startToken = `const ${name} = useCallback(`
  const start = source.indexOf(startToken)
  assert.notEqual(start, -1, `${name} callback should exist`)

  const nextCallback = source.indexOf('\n  const handle', start + startToken.length)
  return source.slice(start, nextCallback === -1 ? source.length : nextCallback)
}

const costChangeSource = extractCallback(pricingHook, 'handleCostChange')
const savePricingSource = extractCallback(pricingHook, 'handleSavePricing')
const resetPricingSource = extractCallback(pricingHook, 'handleResetPricing')

test('pricing source order ranks custom overrides before server config', () => {
  assert.match(pricingHook, /const order = \['user', 'yaml'\]/)
})

test('pricing cost edits prefill the full effective flat rate on first edit', () => {
  assert.doesNotMatch(pricingHook, /costPatches|CostPatch/)
  assert.match(costChangeSource, /prev\[model\.name\] \?\? draftFrom\(pricingData\?\.\[model\.name\]\)/)
})

test('pricing override body requires input/output and nulls blank cache fields', () => {
  assert.match(pricingHook, /if \(field === 'input' \|\| field === 'output'\) return field/)
  assert.match(pricingHook, /body\[keys\[field\]\] = null/)
  assert.match(pricingHook, /!Number\.isFinite\(value\) \|\| value < 0/)
})

test('pricing save PUTs per-model overrides instead of PATCHing config', () => {
  assert.doesNotMatch(pricingHook, /'\/config'/)
  assert.match(savePricingSource, /fetch\(`\/pricing\/overrides\/\$\{path\}`, \{\s*method: 'PUT'/)
  assert.match(savePricingSource, /const path = encodeURIComponent\(model\)/)
})

test('pricing save optionally reprices unpriced usage after each successful PUT', () => {
  const putIndex = savePricingSource.indexOf("method: 'PUT'")
  const repriceIndex = savePricingSource.indexOf('fetch(`/usage/reprice?model=${path}`')
  assert.ok(putIndex !== -1 && repriceIndex > putIndex)
  assert.match(savePricingSource, /if \(repricePast\)/)
  assert.match(savePricingSource, /repriced \+= /)
  // The draft survives a failed save or reprice.
  assert.ok(savePricingSource.indexOf('dropDraft(model)') > repriceIndex)
  assert.match(savePricingSource, /await refetchPricing\(\)/)
})

test('pricing reset deletes the override without repricing and refetches', () => {
  assert.match(resetPricingSource, /method: 'DELETE'/)
  assert.doesNotMatch(resetPricingSource, /\/usage\/reprice/)
  assert.match(resetPricingSource, /await refetchPricing\(\)/)
})

test('adding a listed model matches its existing key case-insensitively', () => {
  assert.match(pricingHook, /name\.trim\(\)\.toLowerCase\(\)/)
  assert.match(pricingHook, /key\.toLowerCase\(\) === model/)
  assert.match(pricingHook, /setPricingSearch\(existing\)/)
})
