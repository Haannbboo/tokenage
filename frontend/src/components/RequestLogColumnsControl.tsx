import { useCallback, useRef, useState } from 'react'
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
  const close = useCallback((event: Event) => {
    setIsOpen(false)
    if (event instanceof KeyboardEvent) triggerRef.current?.focus()
  }, [])
  useDismiss(ref, isOpen, close)

  return (
    <div className="request-log-columns" ref={ref}>
      <button
        type="button"
        ref={triggerRef}
        className="btn-ghost request-log-columns-trigger"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
      >
        {t('Columns')}
      </button>

      {isOpen && (
        <div className="menu request-log-columns-menu">
          <div className="request-log-columns-list">
            {columns.map((column) => (
              <label key={column.id} className="request-log-columns-option">
                <input
                  type="checkbox"
                  checked={selectedColumnIds.includes(column.id)}
                  onChange={(event) => setColumnVisible(column.id, event.target.checked)}
                />
                <span>{t(column.label)}</span>
              </label>
            ))}
          </div>

          <div className="request-log-columns-actions">
            <button
              type="button"
              className="btn-ghost request-log-columns-action"
              onClick={resetColumns}
            >
              {t('Reset columns')}
            </button>
            <button
              type="button"
              className="btn-ghost request-log-columns-action"
              onClick={showAllColumns}
            >
              {t('All columns')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
