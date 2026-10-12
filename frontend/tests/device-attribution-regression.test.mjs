import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import assert from 'node:assert/strict'

const root = join(import.meta.dirname, '..')
const read = (...p) => readFileSync(join(root, 'src', ...p), 'utf-8')

test('request log table has a hideable Device column with an Unknown fallback', () => {
  assert.match(read('hooks', 'useRequestLogColumns.ts'), /id: 'device', label: 'Device'/)
  assert.match(read('pages', 'LogsPage.tsx'), /row\.device_name \?\? t\('Unknown'\)/)
})

test('logs page filters by device_id and lists revoked devices', () => {
  assert.match(read('hooks', 'useLogsData.ts'), /set\('device_id', opts\.deviceFilter\)/)
  assert.match(read('components', 'DeviceSelect.tsx'), /useDevices\(true, true\)/)
  assert.match(read('hooks', 'useDevices.ts'), /include_revoked=true/)
})

test('dashboard top-usage chart can group by device', () => {
  const chart = read('charts', 'TopUsageChart.tsx')
  assert.match(chart, /dimension', 'device'/)
  assert.match(chart, /r\.dimension_name \?\? t\('Unknown'\)/)
})

test('dashboard and logs share one device filter that reaches every dashboard fetch', () => {
  assert.match(read('contexts', 'AppContext.tsx'), /activeDevice, setActiveDevice/)
  assert.match(read('pages', 'DashboardPage.tsx'), /<DeviceSelect value=\{activeDevice\}/)
  assert.match(read('pages', 'LogsPage.tsx'), /<DeviceSelect value=\{activeDevice\}/)
  const data = read('hooks', 'useDashboardData.ts')
  assert.match(data, /url\.searchParams\.set\('device_id', activeDevice\)/)
  assert.match(data, /heatmapUrl\.searchParams\.set\('device_id', activeDevice\)/)
  assert.match(data, /device_id: activeDevice/)
  assert.match(read('charts', 'ToolCallsChart.tsx'), /set\('device_id', filterParams\.device_id\)/)
  assert.match(read('charts', 'TopUsageChart.tsx'), /set\('device_id', filterParams\.device_id\)/)
})

test('logs Reset clears the shared device filter', () => {
  assert.match(read('pages', 'LogsPage.tsx'), /setActiveSource\(null\)\s+setActiveDevice\(null\)/)
})

test('device chart rows are cleared per request and not applied once aborted', () => {
  const chart = read('charts', 'TopUsageChart.tsx')
  assert.match(chart, /setDeviceRows\(\[\]\)/)
  assert.match(chart, /if \(!controller\.signal\.aborted\) setDeviceRows\(rows\)/)
})
