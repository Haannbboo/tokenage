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
import { PageHeader } from '../components/PageHeader'
import { expandableRowProps } from '../expandableRow'

const DEVICE_KIND_LABELS: Record<string, string> = {
  client: 'Machine',
  cli: 'CLI',
  ingest: 'Ingest',
}

export function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const section = searchParams.get('section')
  const activeSection = section && ['pricing', 'services', 'devices'].includes(section) ? section : 'services'
  const { showToast } = useApp()
  const authDevicesActive = activeSection === 'devices'
  const { devices, webSessions, refresh: refreshDevices } = useDevices(authDevicesActive)
  const { statuses } = useDeviceStatuses(authDevicesActive)
  const [expandedDeviceId, setExpandedDeviceId] = useState<string | null>(null)
  const [revokingDeviceId, setRevokingDeviceId] = useState<string | null>(null)

  const deviceReport = (deviceId: string) =>
    statuses?.find((entry) => entry.device_id === deviceId)?.status ?? null

  const handleRevokeDevice = async (deviceId: string, revokedMessage = 'Device revoked') => {
    if (revokingDeviceId !== null) return
    setRevokingDeviceId(deviceId)
    try {
      const response = await fetch(`/auth/devices/${deviceId}/revoke`, { method: 'POST' })
      if (response.ok) {
        showToast(t(revokedMessage))
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
    <div className="settings-page">
      <PageHeader title="Settings" />
      <div className="tab-toggle settings-sections">
        {[
          { id: 'pricing', label: t('Pricing') },
          { id: 'services', label: t('Services') },
          { id: 'devices', label: t('Devices') },
        ].map((section) => (
          <button
            key={section.id}
            type="button"
            aria-pressed={activeSection === section.id}
            className={`tab-toggle-btn ${activeSection === section.id ? 'active' : ''}`}
            onClick={() => setSearchParams({ section: section.id })}
          >
            {section.label}
          </button>
        ))}
      </div>

      {activeSection === 'pricing' && <PricingPage />}

      {activeSection === 'services' && (
        <>
          <TimezoneSelector />


        </>
      )}

      {activeSection === 'devices' && (
        <>
        <div className="panel">
            <div className="panel-tabs">
              <div className="tab active">{t('Machines')}</div>
            </div>
            <div className="panel-body" style={{ padding: '0' }}>
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
                        {...(device.kind === 'client' ? expandableRowProps(expandedDeviceId === device.id, () => setExpandedDeviceId(expandedDeviceId === device.id ? null : device.id)) : {})}
                      >
                        <td style={{ fontWeight: 500 }}>
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
        <div className="panel" style={{ marginTop: '24px' }}>
            <div className="panel-tabs">
              <div className="tab active">{t('Web sessions')}</div>
            </div>
            <div className="panel-body" style={{ padding: '0' }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>{t('Browser')}</th>
                    <th>{t('Signed in')}</th>
                    <th>{t('Last active')}</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {webSessions && webSessions.length > 0 ? webSessions.map((session) => (
                    <tr key={session.id}>
                      <td style={{ fontWeight: 500 }}>
                        {session.device_name}
                        {session.current && (
                          <span className="badge badge-success" style={{ marginLeft: '8px' }}>
                            {t('This session')}
                          </span>
                        )}
                      </td>
                      <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{formatTime(session.created_at)}</td>
                      <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        {session.last_used_at != null ? formatTime(session.last_used_at) : '—'}
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        {!session.current && (
                          <button
                            type="button"
                            className="btn-danger"
                            disabled={revokingDeviceId !== null}
                            onClick={() => void handleRevokeDevice(session.id, 'Session revoked')}
                          >
                            {revokingDeviceId === session.id ? `${t('Revoke')}…` : t('Revoke')}
                          </button>
                        )}
                      </td>
                    </tr>
                  )) : (
                    <tr>
                      <td colSpan={4} style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
                        {webSessions ? t('No web sessions found.') : t('Loading...')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
