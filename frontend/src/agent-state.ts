import type { SetupAgentHealth } from './types'

export type AgentRow = {
  key: string
  label: string
  found: boolean | null
  health: SetupAgentHealth | null
}

export type AgentVerdict = {
  tone: 'badge-neutral' | 'badge-success' | 'badge-error'
  /** Translation key, rendered with `t()`. */
  label: string
  /** Whether the row needs its endpoints spelled out. */
  endpoints: boolean
}

/** One verdict per agent: is its OTLP export wired on this device? */
export function agentVerdict(row: AgentRow): AgentVerdict {
  const health = row.health
  const status = health?.status
  // "Not installed" only when the agent is really absent. The client looks for
  // its command in the PATH captured when the service was installed, so a wired
  // agent can still look missing.
  if (row.found === false && (health == null || status === 'missing_config')) {
    return { tone: 'badge-neutral', label: 'Not installed', endpoints: false }
  }
  switch (status) {
    case 'ready':
      return { tone: 'badge-success', label: 'Ready', endpoints: false }
    case 'wrong_endpoint':
      return { tone: 'badge-error', label: 'Wrong endpoint', endpoints: true }
    case 'missing_config':
      return { tone: 'badge-error', label: 'Missing config', endpoints: true }
    case 'configured':
      return { tone: 'badge-neutral', label: 'Configured', endpoints: false }
    default:
      // A newer client may send a status this build does not know: fall back to
      // the structured fields so a row that needs fixing still shows it.
      if (health && (health.endpoint_matches === false || health.configured === false)) {
        return { tone: 'badge-error', label: 'Unknown', endpoints: true }
      }
      return { tone: 'badge-neutral', label: 'Unknown', endpoints: false }
  }
}
