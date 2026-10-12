import { useDevices } from '../hooks/useDevices'
import { t } from '../i18n/index.ts'

// Shared by the dashboard and logs filters. Revoked devices stay selectable;
// `unknown` selects usage that was never attributed to a device.
export function DeviceSelect({ value, onChange }: { value: string | null; onChange: (id: string | null) => void }) {
  const { devices } = useDevices(true, true)
  return (
    <select
      id="device-filter"
      name="deviceFilter"
      aria-label={t('Device')}
      className="input-plain"
      value={value || ''}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">{t('All Devices')}</option>
      {(devices ?? []).filter(d => d.kind === 'client').map(d => (
        <option key={d.id} value={d.id}>{d.device_name ?? d.id}</option>
      ))}
      <option value="unknown">{t('Unknown')}</option>
    </select>
  )
}
