// @vitest-environment jsdom
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { useProjectsData } from '../src/hooks/useProjectsData'
const state = vi.hoisted(() => ({ refreshTrigger: 0, activeSource: 'codex', activeDevice: 'device-one', activeFilter: { provider: 'openai', models: ['a', 'b'] }, dateRange: '24h', timezone: 'UTC' }))
vi.mock('../src/contexts/AppContext', () => ({ useApp: () => state }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('shares session filters while requesting daily project activity and independent detail data', async () => {
  const requests: URL[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    requests.push(new URL(url, 'http://localhost'))
    return { ok: true, json: async () => ({ key: 'repo / café' }) }
  }))
  const view = renderHook(({ project }) => useProjectsData(project, '30d'), { initialProps: { project: null as string | null } })
  await waitFor(() => expect(view.result.current.list.loading).toBe(false))
  expect(requests).toHaveLength(1)
  const params = requests[0].searchParams
  expect(params.get('granularity')).toBe('day')
  expect(params.get('client_source')).toBe('codex')
  expect(params.get('device_id')).toBe('device-one')
  expect(params.get('provider')).toBe('openai')
  expect(params.getAll('model')).toEqual(['a', 'b'])
  expect(Math.abs(Date.parse(params.get('since')!) - (Date.now() - 30 * 86400_000))).toBeLessThan(2000)
  view.rerender({ project: 'repo / café' })
  await waitFor(() => expect(view.result.current.detail.data?.key).toBe('repo / café'))
  expect(requests).toHaveLength(2)
  expect(requests[1].pathname).toBe('/sessions/projects/detail')
  expect(requests[1].searchParams.get('name')).toBe('repo / café')
  expect(requests[1].searchParams.get('tz_offset')).toBe('+00:00')
})

it('keeps list and detail data visible while refreshing and clears detail when changing projects', async () => {
  let release: ((value: any) => void) | undefined
  let pending = false
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (pending) return new Promise(resolve => { release = resolve })
    return { ok: true, json: async () => url.startsWith('/sessions/projects/detail') ? { key: 'one' } : { projects: [] } }
  }))
  const view = renderHook(({ project }) => useProjectsData(project, '30d'), { initialProps: { project: 'one' } })
  await waitFor(() => expect(view.result.current.detail.data?.key).toBe('one'))
  const originalList = view.result.current.list.data
  const originalDetail = view.result.current.detail.data
  pending = true
  state.refreshTrigger++
  view.rerender({ project: 'one' })
  await waitFor(() => expect(view.result.current.detail.loading).toBe(true))
  expect(view.result.current.list.data).toBe(originalList)
  expect(view.result.current.detail.data).toBe(originalDetail)
  view.rerender({ project: 'two' })
  expect(view.result.current.detail.data).toBeNull()
  release?.({ ok: true, json: async () => ({ key: 'two' }) })
  await waitFor(() => expect(view.result.current.detail.data?.key).toBe('two'))
  state.refreshTrigger = 0
})

it('drops the previous filters data and surfaces the error when the new filters fetch fails', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => (
    url.includes('client_source=claude') ? { ok: false, json: async () => ({}) } : { ok: true, json: async () => ({ projects: [{ name: 'old' }] }) }
  )))
  const view = renderHook(() => useProjectsData(null, '30d'))
  await waitFor(() => expect(view.result.current.list.data).not.toBeNull())
  state.activeSource = 'claude'
  view.rerender()
  await waitFor(() => expect(view.result.current.list.error).toBe(true))
  expect(view.result.current.list.data).toBeNull()
  state.activeSource = 'codex'
})
