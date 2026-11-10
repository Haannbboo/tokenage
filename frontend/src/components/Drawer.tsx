import { useEffect, useId, useRef, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from 'react'
import { t } from '../i18n'
import { PHONE_QUERY, useMediaQuery } from '../hooks/useMediaQuery'

const TABBABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/** Details panel: nonmodal beside the table on wide screens, a full-screen modal on phones. */
export function Drawer({ title, meta, actions, width, onClose, children }: {
  title: string; meta?: ReactNode; actions?: ReactNode; width: 480 | 760
  onClose: () => void; children: ReactNode
}) {
  const titleId = useId()
  const panel = useRef<HTMLElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  const modal = useMediaQuery(PHONE_QUERY)
  const opener = useRef<HTMLElement | null>(null)
  // A keyed replacement must capture focus after the old drawer restores its opener.
  useEffect(() => {
    opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
  }, [])
  // Declared before the focus effect so the background is interactive again when focus returns to the opener.
  useEffect(() => {
    const element = panel.current
    if (!modal || !element) return
    if (!element.contains(document.activeElement)) element.focus()
    // Everything outside the drawer's ancestor chain goes inert (aria-hidden for browsers without inert).
    const hidden = new Map<Element, string | null>()
    const observers: MutationObserver[] = []
    for (let node: Element = element; node.parentElement && node !== document.body; node = node.parentElement) {
      const ancestorChild = node
      const hideSibling = (sibling: Element) => {
        if (sibling === ancestorChild || sibling.hasAttribute('inert') || sibling.matches('[aria-live], [role="status"], [role="alert"], [role="log"]')) return
        hidden.set(sibling, sibling.getAttribute('aria-hidden'))
        sibling.setAttribute('inert', '')
        sibling.setAttribute('aria-hidden', 'true')
      }
      for (const sibling of node.parentElement.children) hideSibling(sibling)
      const observer = new MutationObserver(records => {
        for (const record of records) {
          for (const added of record.addedNodes) {
            if (added instanceof Element && added.parentElement === record.target) hideSibling(added)
          }
        }
      })
      observer.observe(node.parentElement, { childList: true })
      observers.push(observer)
    }
    return () => {
      observers.forEach(observer => observer.disconnect())
      hidden.forEach((ariaHidden, sibling) => {
        sibling.removeAttribute('inert')
        if (ariaHidden === null) sibling.removeAttribute('aria-hidden')
        else sibling.setAttribute('aria-hidden', ariaHidden)
      })
    }
  }, [modal])
  useEffect(() => {
    const element = panel.current
    element?.focus()
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); close.current() }
    }
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('keydown', escape)
      // Switching rows has already moved focus to the next opener.
      if (opener.current?.isConnected && (document.activeElement === document.body || element?.contains(document.activeElement))) opener.current.focus()
    }
  }, [])
  const trapFocus = (event: ReactKeyboardEvent) => {
    if (!modal || event.key !== 'Tab') return
    const items = Array.from(panel.current?.querySelectorAll<HTMLElement>(TABBABLE) ?? [])
      .filter(item => item.tabIndex >= 0 && !item.matches(':disabled') && !item.closest('[inert]') && item.getClientRects().length > 0 && getComputedStyle(item).visibility !== 'hidden')
    const first = items[0], last = items[items.length - 1]
    const active = document.activeElement
    if (!first) event.preventDefault()
    else if (event.shiftKey && (active === first || !items.includes(active as HTMLElement))) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && (active === last || !items.includes(active as HTMLElement))) { event.preventDefault(); first.focus() }
  }
  return <aside ref={panel} role="dialog" aria-modal={modal || undefined} aria-labelledby={titleId} tabIndex={-1} className={`detail-drawer drawer-${width}`} onKeyDown={trapFocus}>
    <header className="detail-drawer-header">
      <div className="detail-drawer-heading"><h2 id={titleId}>{title}</h2><div className="detail-drawer-actions">{actions}<button type="button" className="icon-btn" aria-label={t('Close')} onClick={onClose}>×</button></div></div>
      {meta && <div className="detail-drawer-meta">{meta}</div>}
    </header>
    <div className="detail-drawer-body">{children}</div>
  </aside>
}
