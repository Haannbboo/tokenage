import { Fragment, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { PricingPage } from './PricingPage'
import { useApp } from '../contexts/AppContext'
import { useDevices } from '../hooks/useDevices'
import { useDeviceStatuses } from '../hooks/useDeviceStatuses'
import { t } from '../i18n/index.ts'
import { formatTime } from '../utils'
import { TimezoneSelector } from '../components/TimezoneSelector'
import { DeviceStatusDetail } from '../components/DeviceStatusDetail'
import { useVersion } from '../hooks/useVersion'

const DEVICE_KIND_LABELS: Record<string, string> = {
  client: 'Machine',
  cli: 'CLI',
  ingest: 'Ingest',
}

export function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const section = searchParams.get('section')
  const activeSection = section && ['pricing', 'services', 'devices'].includes(section) ? section : 'services'
  const {
    auth, showToast, signOut,
  } = useApp()
  const versionData = useVersion()
  const authDevicesActive = activeSection === 'devices'
  const { devices, refresh: refreshDevices } = useDevices(authDevicesActive)
  const { statuses } = useDeviceStatuses(authDevicesActive)
  const [expandedDeviceId, setExpandedDeviceId] = useState<string | null>(null)
  const [revokingDeviceId, setRevokingDeviceId] = useState<string | null>(null)

  const deviceReport = (deviceId: string) =>
    statuses?.find((entry) => entry.device_id === deviceId)?.status ?? null

  const handleRevokeDevice = async (deviceId: string) => {
    if (revokingDeviceId !== null) return
    setRevokingDeviceId(deviceId)
    try {
      const response = await fetch(`/auth/devices/${deviceId}/revoke`, { method: 'POST' })
      if (response.ok) {
        showToast(t('Device revoked'))
        refreshDevices()
      } else {
        showToast(t('Failed to revoke device'))
      }
    } catch {
      showToast(t('Failed to revoke device'))
    } finally {
      setRevokingDeviceId(null)
    }
  }

  return (
    <div className="settings-page" style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      <div className="panel" style={{ overflow: 'hidden' }}>
        <div
          style={{
            display: 'flex',
            gap: '4px',
            padding: '8px',
            background: 'var(--tab-toggle-bg)',
            borderRadius: '12px',
            flexWrap: 'wrap',
          }}
        >
          {[
            { id: 'pricing', label: t('Pricing') },
            { id: 'services', label: t('Services') },
            { id: 'devices', label: t('Devices') },
          ].map((section) => (
            <button
              key={section.id}
              type="button"
              className={`tab-toggle-btn ${activeSection === section.id ? 'active' : ''}`}
              onClick={() => setSearchParams({ section: section.id })}
            >
              {section.label}
            </button>
          ))}
        </div>
      </div>

      {activeSection === 'pricing' && <PricingPage />}

      {activeSection === 'services' && (
        <>
          <TimezoneSelector />

          {/* Service Version */}
          <div className="panel">
            <div className="panel-tabs">
              <div className="tab active"><span>🏷️</span> {t('Service Version')}</div>
            </div>
            <div className="panel-body" style={{ padding: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{t('Version')}:</span>
                <span style={{
                  fontSize: '16px',
                  fontWeight: 700,
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-primary)',
                  padding: '4px 12px',
                  background: 'var(--tab-toggle-bg)',
                  borderRadius: '6px',
                }}>
                  {versionData?.version ?? '—'}
                </span>
              </div>
            </div>
          </div>
        </>
      )}

      {activeSection === 'devices' && (
        <div className="panel">
            <div className="panel-tabs" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div className="tab active"><span>💻</span> {t('Devices')}</div>
              {auth.provider === 'google' && auth.user && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <span className="user-avatar">
                    {(auth.user.name || auth.user.email).charAt(0).toUpperCase()}
                  </span>
                  <span className="user-email" title={auth.user.email}>
                    {auth.user.name || auth.user.email}
                  </span>
                  <button className="btn-danger" onClick={() => void signOut()}>
                    {t('Sign out')}
                  </button>
                </div>
              )}
            </div>
            <div className="panel-body" style={{ padding: '0' }}>
              <div style={{ padding: '16px', borderBottom: '1px solid var(--border-color)', fontSize: '13px', color: 'var(--text-secondary)' }}>
                {t('Installed machines and browser sessions. Revoking a machine stops tracking immediately.')}
              </div>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('Device')}</th>
                    <th>{t('Kind')}</th>
                    <th>{t('Installed version')}</th>
                    <th>{t('Created')}</th>
                    <th>{t('Last used')}</th>
                    <th>{t('Status')}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {devices && devices.length > 0 ? devices.map((device) => (
                    <Fragment key={device.id}>
                      <tr
                        className={device.kind === 'client' ? `expandable-row${expandedDeviceId === device.id ? ' expanded' : ''}` : undefined}
                        onClick={device.kind === 'client' ? () => setExpandedDeviceId(expandedDeviceId === device.id ? null : device.id) : undefined}
                      >
                        <td style={{ fontWeight: 700 }}>
                          {device.device_name ?? '—'}
                          {device.current && (
                            <span className="badge badge-success" style={{ marginLeft: '8px' }}>
                              {t('Current device')}
                            </span>
                          )}
                        </td>
                        <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                          {t(DEVICE_KIND_LABELS[device.kind] ?? device.kind)}
                        </td>
                        <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }} title={device.client_commit ?? undefined}>
                          {device.client_version ?? '—'}
                          {device.client_commit && ` (${device.client_commit.slice(0, 7)})`}
                        </td>
                        <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{formatTime(device.created_at)}</td>
                        <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                          {device.last_used_at != null ? formatTime(device.last_used_at) : '—'}
                        </td>
                        <td>
                          <span className={`badge ${device.kind === 'client' || device.current ? 'badge-success' : 'badge-neutral'}`}>
                            {device.kind === 'client' ? t('Authorized') : device.current ? t('Active') : t('Idle')}
                          </span>
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <button
                            type="button"
                            className="btn-danger"
                            disabled={revokingDeviceId !== null}
                            onClick={(event) => {
                              event.stopPropagation()
                              void handleRevokeDevice(device.id)
                            }}
                          >
                            {revokingDeviceId === device.id ? `${t('Revoke')}…` : t('Revoke')}
                          </button>
                        </td>
                      </tr>
                      {expandedDeviceId === device.id && (
                        <tr className="expanded-row">
                          <td colSpan={7}>
                            <div className="expanded-detail" style={{ gridTemplateColumns: '1fr' }}>
                              <DeviceStatusDetail status={deviceReport(device.id)} />
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )) : (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
                        {devices ? t('No devices found.') : t('Loading...')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
      )}
    </div>
  )
}
