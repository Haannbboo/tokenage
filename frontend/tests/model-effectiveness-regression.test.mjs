import { describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const dashboard = readFileSync(join(root, 'src/pages/SessionsTab.tsx'), 'utf8')
const types = readFileSync(join(root, 'src/types.ts'), 'utf8')
const hook = readFileSync(join(root, 'src/hooks/useModelEffectivenessData.ts'), 'utf8')
const css = readFileSync(join(root, 'src/App.css'), 'utf8')
const zh = readFileSync(join(root, 'src/i18n/zh.ts'), 'utf8')

describe('Model effectiveness types and data hook', () => {
  test('model effectiveness response types match backend shape', () => {
    assert.match(types, /export type ModelEffectivenessGroup/)
    assert.match(types, /evaluated_count: number/)
    assert.match(types, /unknown_count: number/)
    assert.match(types, /cost_per_solved: number \| null/)
    assert.match(types, /export type ModelEffectivenessResponse/)
  })

  test('hook fetches model effectiveness by model with dashboard filters', () => {
    assert.match(hook, /export function useModelEffectivenessData/)
    assert.match(hook, /new URL\('\/model-effectiveness'/)
    assert.match(hook, /searchParams\.set\('group_by', 'model'\)/)
    assert.match(hook, /searchParams\.set\('client_source'/)
    assert.match(hook, /searchParams\.set\('since'/)
    assert.match(hook, /searchParams\.set\('until'/)
  })

  test('hook exposes an explicit refresh callback for evaluation mutations', () => {
    assert.match(hook, /const \[modelEffectivenessRefreshKey, setModelEffectivenessRefreshKey\] = useState\(0\)/)
    assert.match(hook, /const refreshModelEffectiveness = useCallback\(\(\) => \{/)
    assert.match(hook, /setModelEffectivenessRefreshKey\(key => key \+ 1\)/)
    assert.match(hook, /modelEffectivenessRefreshKey, setError\]/)
    assert.match(hook, /return \{ modelEffectiveness, modelEffectivenessLoading, refreshModelEffectiveness \}/)
  })
})

describe('Model effectiveness Sessions UI', () => {
  test('dashboard renders the model effectiveness panel before the sessions table', () => {
    const panelIndex = dashboard.indexOf('model-effectiveness-panel')
    const tableIndex = dashboard.indexOf('table sessions-table')
    assert.ok(panelIndex !== -1, 'model effectiveness panel not found')
    assert.ok(tableIndex !== -1, 'sessions table not found')
    assert.ok(panelIndex < tableIndex, 'model effectiveness should appear before sessions table')
  })

  test('dashboard shows evaluated counts, unknown sessions, caveat, and empty state', () => {
    assert.match(dashboard, /t\('Model Effectiveness'\)/)
    assert.match(dashboard, /modelEffectivenessTotals\.evaluated/)
    assert.match(dashboard, /modelEffectivenessTotals\.unknown/)
    assert.match(dashboard, /t\('Small sample — treat this as directional\.'\)/)
    assert.match(dashboard, /t\('No evaluated sessions yet\.'\)/)
    assert.match(dashboard, /t\('Mark a few sessions as solved or failed to compare models on your real tasks\.'\)/)
  })

  test('dashboard renders model rows with solved partial failed and cost per solved', () => {
    assert.match(dashboard, /modelEffectiveness\.groups\.map/)
    assert.match(dashboard, /group\.solved_count/)
    assert.match(dashboard, /group\.partial_count/)
    assert.match(dashboard, /group\.failed_count/)
    assert.match(dashboard, /group\.unknown_count/)
    assert.match(dashboard, /group\.cost_per_solved/)
  })

  test('evaluated display includes no-op classifications without changing solve-rate denominator', () => {
    assert.match(dashboard, /function modelEffectivenessClassifiedCount\(group: ModelEffectivenessGroup\): number/)
    assert.match(dashboard, /return group\.evaluated_count \+ group\.no_op_count/)
    assert.match(dashboard, /formatNumber\(modelEffectivenessClassifiedCount\(group\)\)/)
    assert.match(dashboard, /formatEffectivenessShare\(group\.solved_count, group\.evaluated_count\)/)
  })

  test('dashboard refreshes model effectiveness after session evaluation saves', () => {
    assert.match(dashboard, /refreshModelEffectiveness/)
    assert.match(dashboard, /onEvaluationPersisted=\{refreshModelEffectiveness\}/)
  })
})

describe('Model effectiveness styling and i18n', () => {
  test('panel styles exist', () => {
    assert.match(css, /\.model-effectiveness-panel/)
    assert.match(css, /\.model-effectiveness-table/)
    assert.match(css, /\.model-effectiveness-warning/)
    assert.match(css, /\.model-effectiveness-empty/)
  })

  test('Chinese translations exist', () => {
    const expectedKeys = [
      'Model Effectiveness',
      'Based on',
      'evaluated sessions',
      'unknown',
      'Small sample — treat this as directional.',
      'No evaluated sessions yet.',
      'Mark a few sessions as solved or failed to compare models on your real tasks.',
      'Cost / solved',
    ]

    for (const key of expectedKeys) {
      assert.ok(zh.includes(`'${key}':`), `zh.ts should translate: ${key}`)
    }
  })
})
