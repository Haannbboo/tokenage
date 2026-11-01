import { useState, useRef, useEffect, useMemo } from 'react'
import type { SessionSelectorRow } from '../types'
import { shortSessionId, timeAgo, sessionAgentName } from '../utils'
import { t } from '../i18n/index.ts'

interface SessionSelectorProps {
  sessions: SessionSelectorRow[]
  sessionFilter: string | null
  onChange: (id: string | null) => void
  sourceColors: Record<string, string>
}

export function SessionSelector({ sessions, sessionFilter, onChange, sourceColors }: SessionSelectorProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const grouped = useMemo(() => {
    const map = new Map<string, SessionSelectorRow[]>()
    for (const s of sessions) {
      const arr = map.get(s.client_source) ?? []
      arr.push(s)
      map.set(s.client_source, arr)
    }
    return Array.from(map.entries()).sort(([a]: [string, SessionSelectorRow[]], [b]: [string, SessionSelectorRow[]]) => a.localeCompare(b))
  }, [sessions])

  type FilteredGroup = { source: string; items: SessionSelectorRow[] }

  const filtered = useMemo((): FilteredGroup[] => {
    if (!search.trim()) {
      return grouped.map(([source, items]) => ({ source, items }))
    }
    const q = search.toLowerCase()
    return grouped
      .map(([source, list]) => ({
        source,
        items: list.filter((s: SessionSelectorRow) =>
          s.session_id.toLowerCase().includes(q) ||
          sessionAgentName(s.client_source).toLowerCase().includes(q)
        )
      }))
      .filter((g) => g.items.length > 0)
  }, [grouped, search])

  const selected = sessions.find(s => s.session_id === sessionFilter)

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <div
        className="input-plain"
        onClick={() => { setIsOpen(true); ref.current?.querySelector('input')?.focus() }}
        style={{
          position: 'relative',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          height: 35,
          paddingRight: '28px',
          borderColor: isOpen ? 'var(--color-blue)' : undefined,
          cursor: 'text'
        }}
      >
        {sessionFilter && selected ? (
          <>
            <span style={{
              padding: '2px 6px',
              borderRadius: '4px',
              background: sourceColors[selected.client_source] + '22',
              color: sourceColors[selected.client_source] || 'var(--text-secondary)',
              fontSize: '10px',
              fontWeight: 700,
              flexShrink: 0
            }}>
              {sessionAgentName(selected.client_source)}
            </span>
            <span style={{ fontSize: '12px', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {shortSessionId(sessionFilter)}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); onChange(null); setSearch('') }}
              style={{ marginLeft: 'auto', padding: '0 2px', fontSize: '11px', color: 'var(--text-muted)', cursor: 'pointer', flexShrink: 0 }}
              aria-label={t('Clear session filter')}
            >
              ×
            </button>
          </>
        ) : null}
        <input
          type="text"
          aria-label={t('Search session ID…')}
          placeholder={sessionFilter && selected ? '' : t('Search session ID…')}
          value={search}
          onChange={e => { setSearch(e.target.value); setIsOpen(true) }}
          onFocus={() => setIsOpen(true)}
          autoFocus={false}
          style={{
            flex: 1,
            minWidth: 0,
            background: 'transparent',
            border: 'none',
            outline: 'none',
            font: 'inherit',
            color: 'var(--text-primary)',
            padding: 0
          }}
        />
        <svg aria-hidden="true" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}>
          <path d="m4 6 4 4 4-4" />
        </svg>
      </div>

      {isOpen && (
        <div style={{
          position: 'absolute',
          top: 'calc(100% + 4px)',
          left: 0,
          right: 0,
          zIndex: 100,
          background: 'var(--card-bg)',
          border: '1px solid var(--border-color)',
          borderRadius: '8px',
          boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
          maxHeight: 320,
          overflowY: 'auto',
          padding: '4px'
        }}>
          <button
            onClick={() => { onChange(null); setIsOpen(false); setSearch('') }}
            style={{
              width: '100%',
              padding: '8px 12px',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              borderRadius: '6px',
              background: !sessionFilter ? 'var(--surface-hover)' : 'transparent',
              textAlign: 'left',
              fontSize: '12px',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              marginBottom: '4px'
            }}
          >
            <span>🌐</span>
            <span style={{ fontWeight: 600 }}>{t('All Sessions')}</span>
          </button>

          {filtered.length === 0 && (
            <div style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)', fontSize: '12px' }}>
              {t('No sessions found')}
            </div>
          )}

          {filtered.map(group => (
            <div key={group.source}>
              <div style={{
                padding: '6px 10px',
                fontSize: '10px',
                fontWeight: 700,
                color: sourceColors[group.source] || 'var(--text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                borderTop: '1px solid var(--border-color)',
                marginTop: '4px'
              }}>
                {sessionAgentName(group.source)}
              </div>
              {group.items.map((s: SessionSelectorRow) => (
                <button
                  key={s.session_id}
                  onClick={() => { onChange(s.session_id); setIsOpen(false); setSearch('') }}
                  style={{
                    width: '100%',
                    padding: '6px 10px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    borderRadius: '6px',
                    background: sessionFilter === s.session_id ? 'var(--surface-hover)' : 'transparent',
                    textAlign: 'left',
                    cursor: 'pointer'
                  }}
                >
                  <span style={{
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: sourceColors[group.source] + '22',
                    color: sourceColors[group.source] || 'var(--text-primary)',
                    fontSize: '10px',
                    fontWeight: 600,
                    fontFamily: 'var(--font-mono)'
                  }}>
                    {shortSessionId(s.session_id)}
                  </span>
                  <span style={{ fontSize: '11px', color: 'var(--text-secondary)', fontWeight: 500 }}>
                    {s.request_count} req
                  </span>
                  <span style={{ fontSize: '11px', color: 'var(--text-secondary)', marginLeft: 'auto' }}>
                    {timeAgo(s.started)}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
