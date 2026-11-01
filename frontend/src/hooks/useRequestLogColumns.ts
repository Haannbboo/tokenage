import { useCallback, useEffect, useMemo, useState } from 'react'

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

export const DEFAULT_REQUEST_LOG_COLUMNS = REQUEST_LOG_COLUMNS.map(({ id }) => id).filter((id) => id !== 'device')

function normalizeRequestLogColumnIds(columnIds: unknown, fallback = DEFAULT_REQUEST_LOG_COLUMNS): RequestLogColumnId[] {
  if (!Array.isArray(columnIds)) return fallback

  const requestedColumnIds = new Set(columnIds)
  // Iterate the registry so saved preferences cannot reorder table columns.
  const normalizedColumnIds = REQUEST_LOG_COLUMNS
    .filter(({ id }) => requestedColumnIds.has(id))
    .map(({ id }) => id)

  return normalizedColumnIds.length > 0 ? normalizedColumnIds : fallback
}

type RequestLogColumnPreferences = {
  visibleColumnIds: RequestLogColumnId[]
  pinnedColumnIds: RequestLogColumnId[]
}

const DEFAULT_COLUMN_PREFERENCES: RequestLogColumnPreferences = {
  visibleColumnIds: DEFAULT_REQUEST_LOG_COLUMNS,
  pinnedColumnIds: [],
}

function readStoredRequestLogColumns(): RequestLogColumnPreferences {
  try {
    const raw = localStorage.getItem(REQUEST_LOG_COLUMN_KEY)
    if (!raw) return DEFAULT_COLUMN_PREFERENCES

    const parsed = JSON.parse(raw)
    // Older preferences stored just the enabled column IDs.
    const visibleColumnIds = normalizeRequestLogColumnIds(Array.isArray(parsed) ? parsed : parsed?.visibleColumnIds)
    const pinnedColumnIds = normalizeRequestLogColumnIds(parsed?.pinnedColumnIds, [])
      .filter(id => visibleColumnIds.includes(id))
    return { visibleColumnIds, pinnedColumnIds }
  } catch {
    return DEFAULT_COLUMN_PREFERENCES
  }
}

export function useRequestLogColumns() {
  const [preferences, setPreferences] = useState(readStoredRequestLogColumns)
  const { visibleColumnIds, pinnedColumnIds } = preferences

  const setColumnVisible = useCallback((columnId: RequestLogColumnId, visible: boolean) => {
    setPreferences(current => {
      const visibleColumnIds = normalizeRequestLogColumnIds(visible
        ? [...current.visibleColumnIds, columnId]
        : current.visibleColumnIds.filter(id => id !== columnId))
      const pinnedColumnIds = normalizeRequestLogColumnIds(visible
        ? [...current.pinnedColumnIds, columnId]
        : current.pinnedColumnIds.filter(id => id !== columnId), [])
      return { visibleColumnIds, pinnedColumnIds }
    })
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(REQUEST_LOG_COLUMN_KEY, JSON.stringify(preferences))
    } catch {
      // Ignore persistence failures.
    }
  }, [preferences])

  const resetColumns = useCallback(() => {
    setPreferences(DEFAULT_COLUMN_PREFERENCES)
  }, [])

  const showAllColumns = useCallback(() => {
    const columnIds = REQUEST_LOG_COLUMNS.map(({ id }) => id)
    setPreferences({ visibleColumnIds: columnIds, pinnedColumnIds: columnIds })
  }, [])

  const visibleColumns = useMemo(
    () => REQUEST_LOG_COLUMNS.filter(({ id }) => visibleColumnIds.includes(id)),
    [visibleColumnIds],
  )

  return {
    columns: REQUEST_LOG_COLUMNS,
    visibleColumnIds,
    pinnedColumnIds,
    setColumnVisible,
    visibleColumns,
    resetColumns,
    showAllColumns,
  }
}
