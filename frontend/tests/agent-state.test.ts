import { describe, expect, it } from 'vitest'
import { agentVerdict, type AgentRow } from '../src/agent-state'
import type { SetupAgentHealth } from '../src/types'

const health = (over: Partial<SetupAgentHealth> = {}): SetupAgentHealth => ({
  configured: true,
  endpoint_matches: true,
  configured_endpoint: null,
  expected_endpoint: 'http://127.0.0.1:4318/otlp/v1/logs',
  status: 'ready',
  ...over,
})

const row = (over: Partial<AgentRow> = {}): AgentRow => ({
  key: 'claude', label: 'Claude Code', found: true, health: health(), ...over,
})

describe('agent verdict', () => {
  it('calls a wired agent ready', () => {
    expect(agentVerdict(row())).toEqual({ tone: 'badge-success', label: 'Ready', endpoints: false })
  })

  it('reports an endpoint mismatch even when status is ready', () => {
    expect(agentVerdict(row({ health: health({ endpoint_matches: false }) }))).toEqual({ tone: 'badge-error', label: 'Wrong endpoint', endpoints: true })
  })

  it('shows what to fix when the endpoint is wrong', () => {
    const verdict = agentVerdict(row({
      health: health({ status: 'wrong_endpoint', endpoint_matches: false, configured_endpoint: 'http://127.0.0.1:9999/otlp/v1/logs' }),
    }))
    expect(verdict.tone).toBe('badge-error')
    expect(verdict.label).toBe('Wrong endpoint')
    expect(verdict.endpoints).toBe(true)
  })

  it('asks for a config when the agent is installed without one', () => {
    const verdict = agentVerdict(row({
      health: health({ status: 'missing_config', configured: false, endpoint_matches: false, configured_endpoint: null }),
    }))
    expect(verdict.label).toBe('Missing config')
    expect(verdict.endpoints).toBe(true)
  })

  it('does not call an uninstalled agent a wiring problem', () => {
    const verdict = agentVerdict(row({
      found: false,
      health: health({ status: 'missing_config', configured: false, endpoint_matches: null, configured_endpoint: null }),
    }))
    expect(verdict).toEqual({ tone: 'badge-neutral', label: 'Not installed', endpoints: false })
  })

  it('calls a wired agent ready even when its command is off the service PATH', () => {
    expect(agentVerdict(row({ found: false }))).toEqual({ tone: 'badge-success', label: 'Ready', endpoints: false })
  })

  it('still shows the endpoints for a wrong endpoint on an agent off the PATH', () => {
    const verdict = agentVerdict(row({
      found: false,
      health: health({ status: 'wrong_endpoint', endpoint_matches: false, configured_endpoint: 'http://127.0.0.1:9999/otlp/v1/logs' }),
    }))
    expect(verdict.label).toBe('Wrong endpoint')
    expect(verdict.endpoints).toBe(true)
  })

  it('calls a configured agent without a collector neither ready nor broken', () => {
    const verdict = agentVerdict(row({
      health: health({ status: 'configured', endpoint_matches: null, expected_endpoint: null }),
    }))
    expect(verdict).toEqual({ tone: 'badge-neutral', label: 'Configured', endpoints: false })
  })

  it('surfaces an unknown status as a row that needs fixing', () => {
    const verdict = agentVerdict(row({
      health: health({ status: 'invalid_config', endpoint_matches: false, configured_endpoint: 'http://127.0.0.1:9999/otlp/v1/logs' }),
    }))
    expect(verdict.tone).toBe('badge-error')
    expect(verdict.endpoints).toBe(true)
  })

  it('keeps an unknown status on a healthy agent neutral', () => {
    const verdict = agentVerdict(row({ health: health({ status: 'invalid_config', endpoint_matches: null }) }))
    expect(verdict).toEqual({ tone: 'badge-neutral', label: 'Unknown', endpoints: false })
  })

  it('calls an agent without any report data not installed', () => {
    const verdict = agentVerdict({ key: 'kilo', label: 'Kilo Code', found: false, health: null })
    expect(verdict).toEqual({ tone: 'badge-neutral', label: 'Not installed', endpoints: false })
  })
})
