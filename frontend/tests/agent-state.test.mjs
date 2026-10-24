import test from 'node:test'
import assert from 'node:assert/strict'
import { agentVerdict } from '../src/agent-state.ts'

const health = (over = {}) => ({
  configured: true,
  endpoint_matches: true,
  configured_endpoint: null,
  expected_endpoint: 'http://127.0.0.1:4318/otlp/v1/logs',
  status: 'ready',
  ...over,
})

const row = (over = {}) => ({ key: 'claude', label: 'Claude Code', found: true, health: health(), ...over })

test('a wired agent is ready', () => {
  assert.deepEqual(agentVerdict(row()), { tone: 'badge-success', label: 'Ready', endpoints: false })
})

test('a wrong endpoint shows what to fix', () => {
  const verdict = agentVerdict(row({
    health: health({ status: 'wrong_endpoint', endpoint_matches: false, configured_endpoint: 'http://127.0.0.1:9999/otlp/v1/logs' }),
  }))
  assert.equal(verdict.tone, 'badge-error')
  assert.equal(verdict.label, 'Wrong endpoint')
  assert.equal(verdict.endpoints, true)
})

test('an installed agent without a config asks for one', () => {
  const verdict = agentVerdict(row({
    health: health({ status: 'missing_config', configured: false, endpoint_matches: false, configured_endpoint: null }),
  }))
  assert.equal(verdict.label, 'Missing config')
  assert.equal(verdict.endpoints, true)
})

test('an agent that is not installed is not a wiring problem', () => {
  const verdict = agentVerdict(row({
    found: false,
    health: health({ status: 'missing_config', configured: false, endpoint_matches: null, configured_endpoint: null }),
  }))
  assert.deepEqual(verdict, { tone: 'badge-neutral', label: 'Not installed', endpoints: false })
})

test('a wired agent whose command is off the service PATH is still ready', () => {
  const verdict = agentVerdict(row({ found: false }))
  assert.deepEqual(verdict, { tone: 'badge-success', label: 'Ready', endpoints: false })
})

test('a wrong endpoint on an agent off the service PATH still shows the endpoints', () => {
  const verdict = agentVerdict(row({
    found: false,
    health: health({ status: 'wrong_endpoint', endpoint_matches: false, configured_endpoint: 'http://127.0.0.1:9999/otlp/v1/logs' }),
  }))
  assert.equal(verdict.label, 'Wrong endpoint')
  assert.equal(verdict.endpoints, true)
})

test('a configured agent without a known collector is neither ready nor broken', () => {
  const verdict = agentVerdict(row({
    health: health({ status: 'configured', endpoint_matches: null, expected_endpoint: null }),
  }))
  assert.deepEqual(verdict, { tone: 'badge-neutral', label: 'Configured', endpoints: false })
})

test('an unknown status still surfaces a row that needs fixing', () => {
  const verdict = agentVerdict(row({
    health: health({ status: 'invalid_config', endpoint_matches: false, configured_endpoint: 'http://127.0.0.1:9999/otlp/v1/logs' }),
  }))
  assert.equal(verdict.tone, 'badge-error')
  assert.equal(verdict.endpoints, true)
})

test('an unknown status on a healthy agent stays neutral', () => {
  const verdict = agentVerdict(row({ health: health({ status: 'invalid_config', endpoint_matches: null }) }))
  assert.deepEqual(verdict, { tone: 'badge-neutral', label: 'Unknown', endpoints: false })
})

test('an agent with no report data at all is not installed', () => {
  const verdict = agentVerdict({ key: 'kilo', label: 'Kilo Code', found: false, health: null })
  assert.deepEqual(verdict, { tone: 'badge-neutral', label: 'Not installed', endpoints: false })
})
