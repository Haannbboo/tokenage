import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')
const timeline = readFileSync(join(root, 'src/components/RequestTimeline.tsx'), 'utf8')
const logsPage = readFileSync(join(root, 'src/pages/LogsPage.tsx'), 'utf8')
const css = readFileSync(join(root, 'src/index.css'), 'utf8')

describe('RequestTimeline per-tool color coding', () => {
  test('each tool becomes its own segment colored by tool name', () => {
    // Tool segments are keyed per tool and colored via the shared tool palette.
    assert.match(timeline, /key: `tool:\$\{tc\.tool_use_id\}`/)
    assert.match(timeline, /color: getToolColor\(tc\.tool_name\)\.bg/)
  })

  test('no single fixed-color block for all tools', () => {
    // Regression: the old bar lumped every tool into one green block.
    assert.doesNotMatch(timeline, /background: 'var\(--color-green\)'/)
  })

  test('does not fabricate a total when no timing exists', () => {
    // Regression: a `, 1` floor in the total made rows with no latency/ttft/tools
    // render a bogus 1ms "Output generation" bar.
    assert.doesNotMatch(timeline, /Math\.max\(latency, toolMaxEnd, ttft, 1\)/)
  })

  test('renders a dedicated row with constrained max-width (does not occupy full width)', () => {
    assert.match(timeline, /className="request-timeline"/)
    assert.match(css, /\.request-timeline\s*\{[\s\S]*grid-column:\s*1\s*\/\s*-1/)
    assert.match(css, /\.request-timeline\s*\{[\s\S]*max-width:\s*540px/)
  })

  test('displays sequential TUs one per line and parallel TUs stacked', () => {
    // Sequential TUs: single tool per cluster gets individual row
    assert.match(timeline, /cluster\.length === 1/)
    assert.match(timeline, /type:\s*'tool'/)
    // Parallel TUs: overlapping tools are grouped and stacked
    assert.match(timeline, /type:\s*'parallel_group'/)
    assert.match(timeline, /request-timeline-bar-stacked/)
    assert.match(timeline, /isParallel/)
  })

  test('includes TTFT, Output generation, and total end-to-end latency', () => {
    assert.match(timeline, /id:\s*'ttft'/)
    assert.match(timeline, /id:\s*'generation'/)
    assert.match(timeline, /label:\s*t\('Output generation'\)/)
    assert.match(timeline, /className="request-timeline-total"/)
    assert.match(timeline, /formatLatency\(displayTotal\)/)
    // Total is the drawn span: generation plus the tools that followed it.
    assert.match(timeline, /const displayTotal = totalMs/)
  })

  test('latency is TTFT + generation; tools are laid out after it', () => {
    // Every source reports latency without tool execution, so generation fills
    // [ttft, latency] and tools start where generation ends.
    assert.match(timeline, /const genDuration = Math\.max\(0, latency - genStart\)/)
    assert.match(timeline, /const toolsStart = Math\.max\(latency, ttft\)/)
    assert.match(timeline, /const start = toolsStart \+ Math\.max\(0, tc\.ts! - minTs\)/)
    assert.doesNotMatch(timeline, /start_ms != null/)
    assert.doesNotMatch(timeline, /toolBusyMs/)
  })

  test('sorts rows by their calculated start time', () => {
    assert.match(timeline, /rows\.sort\(\(a, b\) => a\.startMs - b\.startMs\)/)
  })

  test('supports extra components for future extensibility', () => {
    assert.match(timeline, /extraComponents\?: TimelineCustomComponent\[\]/)
  })
})

describe('LogsPage uses RequestTimeline', () => {
  test('expanded row renders the component with latency, ttft, and tool calls', () => {
    assert.match(logsPage, /import \{ RequestTimeline[^}]*\} from '\.\.\/components\/RequestTimeline'/)
    assert.match(logsPage, /<RequestTimeline[\s\S]*latencyMs=\{row\.latency_ms\}[\s\S]*ttftMs=\{row\.ttft_ms\}[\s\S]*toolCalls=\{expandedToolCalls\}/)
  })
})
