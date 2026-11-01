// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useSessionsData } from '../src/hooks/useSessionsData'

const context = vi.hoisted(() => ({ refreshTrigger: 0, setError: vi.fn() }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => context }))
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); context.refreshTrigger = 0 })
it('keeps the page one time bound on later pages and renews it on refresh', async () => {
  const urls: URL[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    urls.push(new URL(input))
    return { ok: true, json: async () => ({ sessions: [] }) }
  }))
  const { result, rerender } = renderHook(() => useSessionsData({ activeSource: null, dateRange: '5h' }))
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
