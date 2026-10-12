import { useCallback, useEffect, useMemo, useState, type SetStateAction } from 'react'

const REQUEST_LOG_COLUMN_KEY = 'tokenage-request-log-columns'

export type RequestLogColumnId =
  | 'time'
  | 'model'
  | 'provider'
  | 'source'
  | 'device'
  | 'session'
  | 'input'
  | 'output'
  | 'cost'
  | 'speed'
  | 'status'
  | 'tool'

type RequestLogColumn = {
  id: RequestLogColumnId
  label: string
}

export const REQUEST_LOG_COLUMNS: RequestLogColumn[] = [
  { id: 'time', label: 'Time' },
  { id: 'model', label: 'Model' },
  { id: 'provider', label: 'Provider' },
  { id: 'source', label: 'Source' },
  { id: 'device', label: 'Device' },
  { id: 'session', label: 'Session' },
  { id: 'input', label: 'Input (Prompt)' },
  { id: 'output', label: 'Output' },
  { id: 'cost', label: 'Cost' },
  { id: 'tool', label: 'Tool' },
  { id: 'speed', label: 'Speed' },
  { id: 'status', label: 'Status' },
]

export const DEFAULT_REQUEST_LOG_COLUMNS = REQUEST_LOG_COLUMNS.map(({ id }) => id)

function normalizeRequestLogColumnIds(columnIds: unknown): RequestLogColumnId[] {
  if (!Array.isArray(columnIds)) return DEFAULT_REQUEST_LOG_COLUMNS

  const requestedColumnIds = new Set(columnIds)
  // Iterate the registry so saved preferences cannot reorder table columns.
  const normalizedColumnIds = REQUEST_LOG_COLUMNS
    .filter(({ id }) => requestedColumnIds.has(id))
    .map(({ id }) => id)

  return normalizedColumnIds.length > 0 ? normalizedColumnIds : DEFAULT_REQUEST_LOG_COLUMNS
}

function readStoredRequestLogColumns(): RequestLogColumnId[] {
  try {
    const raw = localStorage.getItem(REQUEST_LOG_COLUMN_KEY)
    if (!raw) return DEFAULT_REQUEST_LOG_COLUMNS

    const parsed = JSON.parse(raw)
    return normalizeRequestLogColumnIds(parsed)
  } catch {
    return DEFAULT_REQUEST_LOG_COLUMNS
  }
}

export function useRequestLogColumns() {
  const [visibleColumnIds, setRawVisibleColumnIds] = useState<RequestLogColumnId[]>(() => readStoredRequestLogColumns())

  const setVisibleColumnIds = useCallback((nextColumnIds: SetStateAction<RequestLogColumnId[]>) => {
    setRawVisibleColumnIds((current) =>
      normalizeRequestLogColumnIds(typeof nextColumnIds === 'function' ? nextColumnIds(current) : nextColumnIds),
    )
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(REQUEST_LOG_COLUMN_KEY, JSON.stringify(visibleColumnIds))
    } catch {
      // Ignore persistence failures.
    }
  }, [visibleColumnIds])

  const resetColumns = useCallback(() => {
    setRawVisibleColumnIds(normalizeRequestLogColumnIds(DEFAULT_REQUEST_LOG_COLUMNS))
  }, [])

  const visibleColumns = useMemo(
    () => REQUEST_LOG_COLUMNS.filter(({ id }) => visibleColumnIds.includes(id)),
    [visibleColumnIds],
  )

  return {
    columns: REQUEST_LOG_COLUMNS,
    visibleColumnIds,
    setVisibleColumnIds,
    visibleColumns,
    resetColumns,
  }
}
