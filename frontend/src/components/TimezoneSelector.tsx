import { useApp } from '../contexts/AppContext'
import { TIMEZONES, getLocalTimezone } from '../utils'
import { t } from '../i18n/index.ts'

export function TimezoneSelector() {
  const { timezone, setTimezone } = useApp()
  const localTz = getLocalTimezone()

  return (
    <div className="panel">
      <div className="panel-tabs">
        <div className="tab active"><span>🌐</span> {t('Timezone')}</div>
      </div>
      <div className="panel-body" style={{ padding: '16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '16px', flexWrap: 'wrap' }}>
        <div>
          <div id="timezone-label" style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>{t('Display timezone')}</div>
          <div id="timezone-description" style={{ fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' }}>
            {timezone === 'auto'
              ? `${t('Auto-detected:')} ${localTz}`
              : `${t('Browser:')} ${localTz}`}
          </div>
        </div>
        <select
          className="input-plain"
          aria-labelledby="timezone-label"
          aria-describedby="timezone-description"
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          style={{ minWidth: '220px' }}
        >
          <option value="auto">{t('Auto (Browser)')}</option>
          {Object.entries(TIMEZONES).map(([group, zones]) => (
            <optgroup key={group} label={t(group)}>
              {zones.map((zone) => (
                <option key={zone} value={zone}>{zone}</option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
    </div>
  )
}
