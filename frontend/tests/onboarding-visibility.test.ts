// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useOnboarding } from '../src/hooks/useOnboarding'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('pauses hidden tabs and checks immediately when visible again', async () => {
  vi.useFakeTimers()
  let hidden = true
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ total: 0 }) }))
  vi.stubGlobal('fetch', fetcher)
  const onFirstEvent = vi.fn()
  renderHook(() => useOnboarding(0, onFirstEvent))
  await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
  expect(fetcher).not.toHaveBeenCalled()
  hidden = false
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
  expect(fetcher).toHaveBeenCalledTimes(1)
  hidden = true
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await vi.advanceTimersByTimeAsync(20000) })
  expect(fetcher).toHaveBeenCalledTimes(1)
  hidden = false
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it('refreshes usage once and keeps the first request visible until dismissal', async () => {
  vi.useFakeTimers()
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
  const event = { model: 'test-model' }
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({
    ok: true,
    json: async () => url === '/usage/count' ? { total: 1 } : [event],
  })))
  const onFirstEvent = vi.fn()
  const { result, rerender } = renderHook(
    ({ count }) => useOnboarding(count, onFirstEvent), { initialProps: { count: 0 } },
  )

  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(onFirstEvent).toHaveBeenCalledTimes(1)
  rerender({ count: 1 })
  expect(result.current.verificationResult).toEqual(event)
  expect(result.current.showFirstRunOnboarding).toBe(true)
  act(() => result.current.dismissOnboarding())
  expect(result.current.showFirstRunOnboarding).toBe(false)
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(onFirstEvent).toHaveBeenCalledTimes(1)
})
