import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ActiveFilter, UsageSummary } from './types'
import { formatModelName, formatProviderName, getModelIcon, getProviderIcon } from './utils'
import { t } from './i18n/index.ts'
import './model-selector.css'

export function ModelSelector({ activeFilter, summary, onChange }: {
  activeFilter: ActiveFilter
  summary: UsageSummary[]
  providerColors: Record<string, string>
  onChange: (filter: ActiveFilter) => void
}) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const selected = activeFilter?.models ?? []
  const grouped = useMemo(() => {
    const map = new Map<string, UsageSummary[]>()
    const term = search.trim().toLowerCase()
    for (const item of summary) {
      if (term && !`${item.provider} ${formatProviderName(item.provider)} ${item.model} ${formatModelName(item.model)}`.toLowerCase().includes(term)) continue
      const rows = map.get(item.provider) ?? []
      if (!rows.some(row => row.model === item.model)) rows.push(item)
      map.set(item.provider, rows)
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [summary, search])
  useEffect(() => { if (open) searchRef.current?.focus() }, [open])
  useLayoutEffect(() => {
    if (!open) return
    const position = () => {
      const trigger = triggerRef.current?.getBoundingClientRect()
      const popover = popoverRef.current
      if (!trigger || !popover) return
      const width = popover.getBoundingClientRect().width
      const margin = 12
      const preferred = trigger.left + width <= window.innerWidth - margin ? trigger.left : trigger.right - width
      const left = Math.max(margin, Math.min(preferred, window.innerWidth - margin - width))
      popover.style.left = `${left - trigger.left}px`
    }
    position()
    window.addEventListener('resize', position)
    return () => window.removeEventListener('resize', position)
  }, [open])
  const update = (models: string[]) => onChange(models.length ? { models } : null)
  const toggle = (models: string[]) => {
    const allSelected = models.every(model => selected.includes(model))
    update(allSelected ? selected.filter(model => !models.includes(model)) : Array.from(new Set([...selected, ...models])))
  }
  const close = () => { setOpen(false); setSearch(''); triggerRef.current?.focus() }
  const navigate = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') { event.preventDefault(); close(); return }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
    event.preventDefault()
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitemcheckbox"]') ?? [])
    if (!items.length) return
    const current = items.indexOf(document.activeElement as HTMLButtonElement)
    const next = current < 0 ? (event.key === 'ArrowDown' ? 0 : items.length - 1) : (current + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length
    items[next].focus()
  }
  return <div className="model-selector">
    <button ref={triggerRef} className="input-plain model-selector-trigger" aria-haspopup="menu" aria-expanded={open} onClick={() => { if (open) close(); else setOpen(true) }}>
      <span className="model-selector-trigger-label">
        {selected.length ? <><span className="model-selector-icons">{selected.slice(0, 3).map(model => <span key={model}>{getModelIcon(model)}</span>)}</span><span>{selected.length === 1 ? formatModelName(selected[0]) : `${selected.length} models`}</span></> : <><span>🌐</span><span>{t('All Models')}</span></>}
      </span><svg className="model-selector-chevron" aria-hidden="true" width="10" height="10" viewBox="0 0 10 10"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.3" /></svg>
    </button>
    {open && <>
      <div className="model-selector-backdrop" onClick={close} />
      <div ref={popoverRef} className="model-selector-popover" onKeyDown={navigate}>
        <div className="model-selector-search"><span aria-hidden="true">⌕</span><input ref={searchRef} value={search} onChange={e => setSearch(e.target.value)} placeholder="Search models or providers" aria-label="Search models or providers" /></div>
        <div className="model-selector-meta"><span>{selected.length} selected</span><button onClick={() => update([])} disabled={!selected.length}>Clear all</button></div>
        <div ref={menuRef} role="menu" aria-label="Models" className="model-selector-list">
          {!grouped.length && <div className="model-selector-empty">No models found</div>}
          {grouped.map(([provider, rows]) => {
            // Select all means the whole provider, including models hidden by search.
            const allModels = Array.from(new Set(summary.filter(row => row.provider === provider).map(row => row.model)))
            const checked = allModels.every(model => selected.includes(model))
            const partial = !checked && allModels.some(model => selected.includes(model))
            return <div className="model-selector-group" key={provider}>
              <button type="button" role="menuitemcheckbox" aria-checked={partial ? 'mixed' : checked} onClick={() => toggle(allModels)} className="model-selector-row model-selector-provider">
                <span className="model-selector-check" aria-hidden="true">{checked ? '✓' : partial ? '−' : ''}</span>{getProviderIcon(provider)}<span>{formatProviderName(provider)}</span>
              </button>
              {rows.map(row => <button type="button" role="menuitemcheckbox" aria-checked={selected.includes(row.model)} key={row.model} onClick={() => toggle([row.model])} className="model-selector-row model-selector-model">
                <span className="model-selector-check" aria-hidden="true">{selected.includes(row.model) ? '✓' : ''}</span>{getModelIcon(row.model)}<span>{formatModelName(row.model)}</span>
              </button>)}
            </div>
          })}
        </div>
      </div>
    </>}
  </div>
}
