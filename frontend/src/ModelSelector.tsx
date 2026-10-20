import { useMemo, useState } from 'react'
import type { ActiveFilter, UsageSummary } from './types'
import { formatModelName, getModelIcon, getProviderColor } from './utils'
import { t } from './i18n/index.ts'

export function ModelSelector({
  activeFilter,
  summary,
  providerColors,
  onChange
}: {
  activeFilter: ActiveFilter,
  summary: UsageSummary[],
  providerColors: Record<string, string>,
  onChange: (filter: ActiveFilter) => void
}) {
  const [isOpen, setIsOpen] = useState(false);

  const grouped = useMemo(() => {
    const map = new Map<string, UsageSummary[]>();
    for (const s of summary) {
      const arr = map.get(s.provider) ?? [];
      arr.push(s);
      map.set(s.provider, arr);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [summary]);

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="input-plain"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          minWidth: '180px',
          background: 'var(--input-bg)',
          justifyContent: 'space-between'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {activeFilter ? (
            activeFilter.model ? (
              <>
                {getModelIcon(activeFilter.model)}
                <span style={{ fontSize: '13px' }}>{formatModelName(activeFilter.model)}</span>
              </>
            ) : (
              <>
              <span style={{
                padding: '2px 8px',
                borderRadius: '4px',
                display: 'inline-flex',
                fontSize: '10px',
                backgroundColor: getProviderColor(activeFilter.provider, providerColors) + '22',
                color: getProviderColor(activeFilter.provider, providerColors),
                fontWeight: 600,
                border: `1px solid ${getProviderColor(activeFilter.provider, providerColors)}44`
              }}>
                {activeFilter.provider}
              </span>
              </>
            )
          ) : (
            <>
              <span>🌐</span>
              <span style={{ fontWeight: 600 }}>{t('All Models')}</span>
            </>
          )}
        </div>
        <span style={{ fontSize: '10px' }}>▼</span>
      </button>

      {isOpen && (
        <>
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 40 }}
            onClick={() => setIsOpen(false)}
          />
          <div style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: '4px',
            background: 'var(--card-bg)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-color)',
            borderRadius: '8px',
            boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            zIndex: 50,
            minWidth: '240px',
            maxHeight: '360px',
            overflowY: 'auto',
            padding: '4px'
          }}>
            <button
              onClick={() => { onChange(null); setIsOpen(false); }}
              style={{
                width: '100%',
                padding: '8px 12px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                borderRadius: '6px',
                background: !activeFilter ? 'var(--surface-hover)' : 'transparent',
                textAlign: 'left'
              }}
            >
              <span>🌐</span> {t('All Models')}
            </button>
            {grouped.map(([provider, models]) => (
              <div key={provider}>
                <button
                  onClick={() => { onChange({ provider, model: null }); setIsOpen(false); }}
                  style={{
                    width: '100%',
                    padding: '8px 12px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    borderRadius: '6px',
                    background: activeFilter?.provider === provider && activeFilter.model === null ? 'var(--surface-hover)' : 'transparent',
                    textAlign: 'left',
                    borderTop: '1px solid var(--border-color)',
                    marginTop: '4px',
                    paddingTop: '10px'
                  }}
                >
                  <span style={{
                    padding: '2px 8px',
                    borderRadius: '4px',
                    display: 'inline-flex',
                    fontSize: '10px',
                    backgroundColor: (getProviderColor(provider, providerColors)) + '22',
                    color: getProviderColor(provider, providerColors),
                    fontWeight: 600,
                    border: `1px solid ${getProviderColor(provider, providerColors)}44`
                  }}>
                    {provider}
                  </span>
                  <span style={{ fontWeight: 400, color: 'var(--text-muted)', marginLeft: 'auto', fontSize: '11px' }}>
                    {models.length} {t('model')}
                  </span>
                </button>
                {models.map(s => (
                  <button
                    key={`${s.provider}:${s.model}`}
                    onClick={() => { onChange({ provider: s.provider, model: s.model }); setIsOpen(false); }}
                    style={{
                      width: '100%',
                      padding: '6px 12px 6px 32px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      borderRadius: '6px',
                      background: activeFilter?.model === s.model ? 'var(--surface-hover)' : 'transparent',
                      textAlign: 'left',
                      fontSize: '13px'
                    }}
                  >
                    {getModelIcon(s.model)}
                    <span>{formatModelName(s.model)}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
