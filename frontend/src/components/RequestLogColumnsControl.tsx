import { useState } from 'react'
import type { RequestLogColumnId } from '../hooks/useRequestLogColumns'
import { t } from '../i18n/index.ts'

type RequestLogColumn = {
  id: RequestLogColumnId
  label: string
}

type Props = {
  columns: RequestLogColumn[]
  selectedColumnIds: RequestLogColumnId[]
  displayedColumnIds: RequestLogColumnId[]
  setColumnVisible: (columnId: RequestLogColumnId, visible: boolean) => void
  resetColumns: () => void
  showAllColumns: () => void
}

export function RequestLogColumnsControl({
  columns,
  selectedColumnIds,
  displayedColumnIds,
  setColumnVisible,
  resetColumns,
  showAllColumns,
}: Props) {
  const [isOpen, setIsOpen] = useState(false)

  return (
    <div className="request-log-columns">
      <button
        type="button"
        className="btn-ghost request-log-columns-trigger"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((open) => !open)}
      >
        {t('Columns')}
      </button>

      {isOpen && (
        <div className="request-log-columns-menu">
          <div className="request-log-columns-list">
            {columns.map((column) => (
              <label key={column.id} className="request-log-columns-option">
                <input
                  type="checkbox"
                  checked={displayedColumnIds.includes(column.id)}
                  onChange={(event) => setColumnVisible(column.id, event.target.checked)}
                />
                <span>{t(column.label)}</span>
                {selectedColumnIds.includes(column.id) && !displayedColumnIds.includes(column.id) && (
                  <span style={{ color: 'var(--text-muted)', fontSize: '11px' }}>{t('hidden: narrow window')}</span>
                )}
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
