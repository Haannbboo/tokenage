import { Fragment, type CSSProperties } from 'react'
import { t } from '../i18n/index.ts'
import { getAgentDisplayName, getSetupAgentKey } from '../utils'
import { agentVerdict, type AgentRow } from '../agent-state.ts'
import type { SetupAgentHealth } from '../types'

type Props = {
  status: Record<string, any> | null
}

// The agent list: one column per field, sized to its content so the rows stay
// tight instead of stretching across the panel.
const gridStyle: CSSProperties = {
  display: 'grid',
  gridTemplateColumns: 'minmax(120px, max-content) max-content minmax(0, max-content)',
  columnGap: 0,
  rowGap: '4px',
  alignItems: 'center',
  marginTop: '18px',
}

const columnHeaderStyle: CSSProperties = {
  fontSize: '10px',
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  color: 'var(--text-muted)',
  paddingBottom: '8px',
}

const cellStyle: CSSProperties = {
  fontSize: '13px',
  paddingTop: '9px',
  paddingBottom: '9px',
  // Stretch so every cell of a row ends on the same line; only the content
  // is centred.
  alignSelf: 'stretch',
  display: 'flex',
  alignItems: 'center',
}

const rowBorder = '1px solid var(--border-color)'

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
      <div style={{ padding: '16px', fontSize: '13px', color: 'var(--text-muted)' }}>
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

  return (
    <div
      className="panel"
      style={{
        border: '1px solid var(--border-color)',
        borderRadius: '14px',
        boxShadow: 'var(--shadow-soft)',
        padding: '18px 20px',
        width: 'fit-content',
        maxWidth: '1000px',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div
          style={{
            width: '36px',
            height: '36px',
            borderRadius: '10px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'var(--tab-active-bg)',
            color: 'var(--color-blue)',
            flexShrink: 0,
          }}
        >
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)' }}>
            {t('OTLP Tracking Setup')}
          </div>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '2px' }}>
            {t('Detected from your local config and available commands.')}
          </div>
        </div>
      </div>

      {rows.length > 0 ? (
        <div style={gridStyle}>
          <span style={{ ...columnHeaderStyle, paddingRight: '20px', borderBottom: '1px solid var(--border-color)' }}>{t('Agent')}</span>
          <span style={{ ...columnHeaderStyle, paddingRight: '20px', borderBottom: '1px solid var(--border-color)' }}>{t('Status')}</span>
          <span style={{ alignSelf: 'stretch', borderBottom: '1px solid var(--border-color)' }} />
          {rows.map((row, index) => {
            const state = agentVerdict(row)
            const last = index === rows.length - 1
            // Only spell out endpoints when there is something to compare.
            const showEndpoints = state.endpoints && !!(row.health?.expected_endpoint || row.health?.configured_endpoint)
            return (
              <Fragment key={row.key}>
                <span style={{ ...cellStyle, fontWeight: 600, color: 'var(--text-primary)', paddingRight: '20px', borderBottom: last ? 'none' : rowBorder }}>
                  {row.label}
                </span>
                <span style={{ ...cellStyle, paddingRight: '20px', borderBottom: last ? 'none' : rowBorder }}>
                  <span className={`badge ${state.tone}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                    <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'currentColor' }} />
                    {t(state.label)}
                  </span>
                </span>
                {showEndpoints ? (
                  <div style={{ ...cellStyle, display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '3px', minWidth: 0, borderBottom: last ? 'none' : rowBorder }}>
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'baseline', minWidth: 0 }}>
                      <span style={endpointLabelStyle}>{t('Expected endpoint')}</span>
                      <code style={endpointValueStyle}>{row.health?.expected_endpoint ?? '—'}</code>
                    </div>
                    {row.health?.configured_endpoint && (
                      <div style={{ display: 'flex', gap: '10px', alignItems: 'baseline', minWidth: 0 }}>
                        <span style={endpointLabelStyle}>{t('Configured endpoint')}</span>
                        <code style={{ ...endpointValueStyle, color: 'var(--badge-error-text)' }}>{row.health.configured_endpoint}</code>
                      </div>
                    )}
                  </div>
                ) : (
                  <span style={{ ...cellStyle, borderBottom: last ? 'none' : rowBorder }} />
                )}
              </Fragment>
            )
          })}
        </div>
      ) : (
        <div style={{ padding: '20px 0 4px', textAlign: 'center', fontSize: '13px', color: 'var(--text-muted)' }}>
          {t('No local Agent')}
        </div>
      )}
    </div>
  )
}
