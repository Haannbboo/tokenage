import { useEffect, useId, useRef, type ReactNode } from 'react'
import { t } from '../i18n'

/** Nonmodal details panel: the underlying table remains available. */
export function Drawer({ title, meta, actions, width, onClose, children }: {
  title: string; meta?: ReactNode; actions?: ReactNode; width: 480 | 760
  onClose: () => void; children: ReactNode
}) {
  const titleId = useId()
  const panel = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const element = panel.current
    element?.focus()
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); close.current() }
    }
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('keydown', escape)
      // Switching rows has already moved focus to the next opener.
      if (opener?.isConnected && (document.activeElement === document.body || element?.contains(document.activeElement))) opener.focus()
    }
  }, [])
  return <aside ref={panel} role="dialog" aria-labelledby={titleId} tabIndex={-1} className={`detail-drawer drawer-${width}`}>
    <header className="detail-drawer-header">
      <div className="detail-drawer-heading"><h2 id={titleId}>{title}</h2><div className="detail-drawer-actions">{actions}<button type="button" className="icon-btn" aria-label={t('Close')} onClick={onClose}>×</button></div></div>
      {meta && <div className="detail-drawer-meta">{meta}</div>}
    </header>
    <div className="detail-drawer-body">{children}</div>
  </aside>
}
