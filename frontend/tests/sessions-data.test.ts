// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useSessionsData } from '../src/hooks/useSessionsData'

const context = vi.hoisted(() => ({ refreshTrigger: 0, setError: vi.fn(), activeFilter: null, activeDevice: null, timezone: 'UTC' }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => context }))
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); context.refreshTrigger = 0 })
it('keeps the page one time bound on later pages and renews it on refresh', async () => {
  const urls: URL[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = new URL(input, window.location.origin)
    if (url.pathname === '/sessions') urls.push(url)
    return { ok: true, json: async () => ({ sessions: [] }) }
  }))
  const { result, rerender } = renderHook(() => useSessionsData({ activeSource: null, dateRange: '5h', bucket: null }))
  await waitFor(() => expect(urls).toHaveLength(1))
  const firstSince = urls[0].searchParams.get('since')
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 3_600_000)
  act(() => result.current.setSessionPage(2))
  await waitFor(() => expect(urls).toHaveLength(2))
  expect(urls[1].searchParams.get('since')).toBe(firstSince)
  context.refreshTrigger++
  rerender()
  await waitFor(() => expect(urls).toHaveLength(3))
  expect(urls[2].searchParams.get('offset')).toBe('0')
  expect(urls[2].searchParams.get('since')).not.toBe(firstSince)
  vi.restoreAllMocks()
})

it('refreshing a paged list returns to page one without duplicating rows', async () => {
  const page = (from: number) => Array.from({ length: 50 }, (_, i) => ({ session_id: `s${from + i}` }))
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    const url = new URL(input, window.location.origin)
    return { ok: true, json: async () => ({ sessions: url.pathname === '/sessions' ? page(Number(url.searchParams.get('offset'))) : [] }) }
  }))
  const { result, rerender } = renderHook(() => useSessionsData({ activeSource: null, dateRange: 'all', bucket: null }))
  await waitFor(() => expect(result.current.sessions).toHaveLength(50))
  act(() => result.current.setSessionPage(2))
  await waitFor(() => expect(result.current.sessions).toHaveLength(100))
  context.refreshTrigger++
  rerender()
  await waitFor(() => expect(result.current.sessions.map(s => s.session_id)).toEqual(page(0).map(s => s.session_id)))
})
