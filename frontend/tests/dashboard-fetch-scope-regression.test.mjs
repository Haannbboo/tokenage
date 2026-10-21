import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (...parts) => readFileSync(join(here, 'src', ...parts), 'utf-8')

test('logs page skips the dashboard endpoints it never renders', () => {
  const logsSource = read('pages', 'LogsPage.tsx')
  assert.match(logsSource, /useDashboardData\(\{ includeDaily: false, includeHeatmap: false, includeCount: false \}\)/)
})

test('dashboard data hook only fetches the requested endpoints', () => {
  const hookSource = read('hooks', 'useDashboardData.ts')
  assert.match(hookSource, /includeDaily \? fetch\(dailyUrl\.toString\(\), sig\) : null/)
  assert.match(hookSource, /includeHeatmap \? fetch\(heatmapUrl\.toString\(\), sig\) : null/)
  assert.match(hookSource, /includeCount \? fetch\(totalCountUrl\.toString\(\), sig\) : null/)
})

test('dashboard page keeps requesting every endpoint by default', () => {
  assert.match(read('pages', 'DashboardPage.tsx'), /} = useDashboardData\(\)/)
  assert.match(read('hooks', 'useDashboardData.ts'), /includeDaily = true, includeHeatmap = true, includeCount = true/)
})
