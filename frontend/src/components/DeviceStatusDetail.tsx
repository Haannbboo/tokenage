import type { CSSProperties } from 'react'
import { t } from '../i18n/index.ts'
import { getAgentDisplayName, getSetupAgentKey } from '../utils'
import { agentVerdict, type AgentRow } from '../agent-state.ts'
import type { SetupAgentHealth } from '../types'

type Props = {
  status: Record<string, any> | null
}

const endpointLabelStyle: CSSProperties = {
  fontSize: '10px',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.04em',
  color: 'var(--text-muted)',
  whiteSpace: 'nowrap',
}

const endpointValueStyle: CSSProperties = {
  fontSize: '11px',
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-secondary)',
  overflowWrap: 'anywhere',
}

export function DeviceStatusDetail({ status }: Props) {
  if (!status) {
    return (
      <div style={{ gridColumn: '1 / -1', fontSize: '13px', color: 'var(--text-muted)' }}>
        {t('No report yet. Start the tokenage client service on this device.')}
      </div>
    )
  }

  const detected = (status.detected ?? {}) as Record<string, { found?: boolean }>
  const health = (status.agents ?? {}) as Record<string, SetupAgentHealth>

  // Detection and OTLP wiring are one row per agent: the agent you can run,
  // next to whether its OTLP export points at this server.
  const rows: AgentRow[] = []
  const seen = new Set<string>()
  for (const [name, info] of Object.entries(detected)) {
    const key = getSetupAgentKey(name)
    if (seen.has(key)) continue
    seen.add(key)
    rows.push({ key, label: getAgentDisplayName(name), found: info?.found === true, health: health[key] ?? null })
  }
  for (const [key, agentHealth] of Object.entries(health)) {
    if (seen.has(key)) continue
    rows.push({ key, label: getAgentDisplayName(key), found: null, health: agentHealth })
  }
  rows.sort((a, b) => a.label.localeCompare(b.label))

  // Rendered straight into the expanded row's `.expanded-detail` grid: one
  // cell per agent, so the agents wrap across the row width.
  return (
    <>
      <span className="detail-label" style={{ gridColumn: '1 / -1' }}>{t('OTLP Tracking Setup')}</span>
      {rows.length > 0 ? rows.map(row => {
        const state = agentVerdict(row)
        // Only spell out endpoints when there is something to compare.
        const showEndpoints = state.endpoints && !!(row.health?.expected_endpoint || row.health?.configured_endpoint)
        return (
          <div key={row.key} style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>{row.label}</span>
              <span className={`badge ${state.tone}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'currentColor' }} />
                {t(state.label)}
              </span>
            </div>
            {showEndpoints && (
              <>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1px', minWidth: 0 }}>
                  <span style={endpointLabelStyle}>{t('Expected endpoint')}</span>
                  <code style={endpointValueStyle}>{row.health?.expected_endpoint ?? '—'}</code>
                </div>
                {row.health?.configured_endpoint && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '1px', minWidth: 0 }}>
                    <span style={endpointLabelStyle}>{t('Configured endpoint')}</span>
                    <code style={{ ...endpointValueStyle, color: 'var(--badge-error-text)' }}>{row.health.configured_endpoint}</code>
                  </div>
                )}
              </>
            )}
          </div>
        )
      }) : (
        <span style={{ gridColumn: '1 / -1', fontSize: '13px', color: 'var(--text-muted)' }}>{t('No local Agent')}</span>
      )}
    </>
  )
}
