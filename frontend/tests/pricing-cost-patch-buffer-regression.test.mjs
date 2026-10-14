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

test('pricing cost edits keep keystroke path out of YAML serialization', () => {
  assert.doesNotMatch(costChangeSource, /yaml\.dump\(/)
  assert.doesNotMatch(costChangeSource, /configParsed/)
  assert.match(costChangeSource, /setCostPatches\(/)
})

test('pricing cost edit patch buffer records set and delete operations', () => {
  assert.match(pricingHook, /type CostPatchOp = 'set' \| 'delete'/)
  assert.match(pricingHook, /type CostPatch = \{ path: string\[\]; op: CostPatchOp; value\?: number \}/)
  assert.match(costChangeSource, /const op: CostPatchOp = val === '' \? 'delete' : 'set'/)
  assert.match(costChangeSource, /Number\(val\)/)
  assert.match(costChangeSource, /Number\.isFinite\(numValue\)/)
  assert.match(costChangeSource, /costPathPrefix\(model\)/)
  assert.match(pricingHook, /\['models', model, 'cost'\]/)
})

test('pricing save routes to PATCH config endpoint', () => {
  assert.match(savePricingSource, /method: 'PATCH'/)
  assert.match(savePricingSource, /body: JSON\.stringify\(\{ patches: costPatches \}\)/)
})

test('pricing save clears the buffer after a successful save and refetches pricing', () => {
  assert.doesNotMatch(savePricingSource, /const configResp = await fetch\('\/config'\)/)

  const clearIndex = savePricingSource.indexOf('setCostPatches([])')
  const refetchIndex = savePricingSource.indexOf("fetch('/pricing')")
  assert.ok(clearIndex !== -1 && refetchIndex !== -1)
  assert.ok(clearIndex < refetchIndex, 'patch buffer should clear before refetching pricing')

  assert.match(savePricingSource, /setPricingData\(await pricingResp\.json\(\)\)/)
})
