import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { useDismiss } from '../hooks/useDismiss'
import type { RequestLogColumnId } from '../hooks/useRequestLogColumns'
import { t } from '../i18n/index.ts'

type RequestLogColumn = {
  id: RequestLogColumnId
  label: string
}

type Props = {
  columns: RequestLogColumn[]
  selectedColumnIds: RequestLogColumnId[]
  setColumnVisible: (columnId: RequestLogColumnId, visible: boolean) => void
  resetColumns: () => void
  showAllColumns: () => void
}

/** Arrow keys move focus through the column rows, wrapping at the ends; Home and End jump to them. */
function navigate(event: KeyboardEvent<HTMLElement>) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemcheckbox"]'))
  const current = items.indexOf(document.activeElement as HTMLButtonElement)
  const step = event.key === 'ArrowDown' ? 1 : -1
  const next = event.key === 'Home' ? 0
    : event.key === 'End' ? items.length - 1
    : current < 0 ? (step > 0 ? 0 : items.length - 1) : (current + step + items.length) % items.length
  items[next]?.focus()
}

export function RequestLogColumnsControl({
  columns,
  selectedColumnIds,
  setColumnVisible,
  resetColumns,
  showAllColumns,
}: Props) {
  const [isOpen, setIsOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuId = useId()
  const close = useCallback((event: Event) => {
    setIsOpen(false)
    if (event instanceof KeyboardEvent) triggerRef.current?.focus()
  }, [])
  useDismiss(ref, isOpen, close)
  useEffect(() => {
    if (isOpen) ref.current?.querySelector<HTMLElement>('[role="menuitemcheckbox"]')?.focus()
  }, [isOpen])

  return (
    <div
      className="request-log-columns"
      ref={ref}
      // Tab or Shift+Tab out of the control closes the menu; a click outside is useDismiss's.
      onBlur={(event) => {
        if (event.relatedTarget instanceof Node && !ref.current?.contains(event.relatedTarget)) setIsOpen(false)
      }}
    >
      <button
        type="button"
        ref={triggerRef}
        className="input-plain request-log-columns-trigger"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-controls={isOpen ? menuId : undefined}
        onClick={() => setIsOpen((open) => !open)}
      >
        {t('Columns')}
        <svg aria-hidden="true" width="10" height="10" viewBox="0 0 10 10"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" strokeWidth="1.3" /></svg>
      </button>

      {isOpen && (
        <div id={menuId} className="request-log-columns-menu" onKeyDown={navigate}>
          <div role="menu" aria-label={t('Columns')} className="request-log-columns-list">
            {columns.map((column) => {
              const checked = selectedColumnIds.includes(column.id)
              return (
                <button
                  key={column.id}
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={checked}
                  className="request-log-columns-row"
                  onClick={() => setColumnVisible(column.id, !checked)}
                >
                  <span className="request-log-columns-mark" aria-hidden="true">{checked ? '✓' : ''}</span>
                  <span>{t(column.label)}</span>
                </button>
              )
            })}
          </div>
          <div className="request-log-columns-actions">
            <button type="button" onClick={resetColumns}>{t('Reset')}</button>
            <button type="button" onClick={showAllColumns}>{t('All columns')}</button>
          </div>
        </div>
      )}
    </div>
  )
}
