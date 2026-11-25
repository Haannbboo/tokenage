// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, expect, it, vi } from 'vitest'
import { TopUsageChart } from '../src/charts/TopUsageChart'
import { SourceFilter } from '../src/components/PageHeader'
import { renderRequestCell } from '../src/components/RequestCell'
import { RequestDetail } from '../src/components/RequestDetail'
import type { UsageRow } from '../src/types'
import { getAgentDisplayName, getSetupAgentKey, getSourceIcon, sessionAgentName, sourceColor } from '../src/utils'

vi.mock('../src/contexts/AppContext', () => ({ useApp: () => ({ timezone: 'UTC' }) }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

const iconPath = '/models/google-antigravity.png'

it.each(['agy', 'AGY'])('uses the Antigravity identity for %s', source => {
  expect(getAgentDisplayName(source)).toBe('Antigravity')
  expect(sessionAgentName(source)).toBe('Antigravity')
  expect(getSetupAgentKey(source)).toBe('agy')
  expect(sourceColor(source)).toBe('var(--hue-indigo)')
  expect(renderToStaticMarkup(getSourceIcon(source, 'light'))).toContain(`<img src="${iconPath}"`)
  expect(renderToStaticMarkup(getSourceIcon(source, 'dark'))).toContain(`<img src="${iconPath}"`)
})

it.each(['my-agy-fork', 'Google Antigravity'])('does not use the Antigravity icon for %s', source => {
  expect(getSourceIcon(source, 'light')).toBeNull()
})

it('labels the source filter without changing its value or other agent labels', () => {
  const onChange = vi.fn()
  render(createElement(SourceFilter, { value: null, sources: ['agy', 'claude-code'], onChange }))
  expect((screen.getByRole('option', { name: 'Antigravity' }) as HTMLOptionElement).value).toBe('agy')
  expect(screen.getByRole('option', { name: 'claude-code' })).toBeTruthy()
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'agy' } })
  expect(onChange).toHaveBeenCalledWith('agy')
})

it('labels the Antigravity source in the Logs table and request detail', () => {
  const row = { id: 'request-1', client_source: 'agy', endpoint: '/v1' } as UsageRow
  const cell = renderRequestCell(row, 'source', {
    colWidths: { model: 120, device: 120 },
    hoveredSession: null,
    setSessionFilter: vi.fn(),
    recalculatingId: null,
    handleRecalculateCost: async () => {},
  })
  render(createElement('table', null, createElement('tbody', null, createElement('tr', null, cell))))
  const source = screen.getByRole('cell', { name: 'Antigravity' })
  expect(source.title).toBe('Antigravity')
  expect(source.querySelector('img')?.getAttribute('src')).toBe(iconPath)

  render(createElement(RequestDetail, { row, toolCalls: null, showToast: vi.fn() }))
  expect(screen.getByText('Antigravity').querySelector('img')?.getAttribute('src')).toBe(iconPath)
})

it('labels the Top Sources row and its tooltip while sending the raw key', async () => {
  const fetchSource = vi.fn(async () => ({
    ok: true, json: async () => [{ client_source: 'agy', total_tokens: 12 }],
  }))
  vi.stubGlobal('fetch', fetchSource)
  render(createElement(TopUsageChart, { summary: [], theme: 'light', filterParams: { client_source: 'agy' } }))
  fireEvent.click(screen.getByRole('button', { name: 'Sources' }))
  const row = (await screen.findByText('Antigravity')).closest('li')!
  expect(row.querySelector('img')?.getAttribute('src')).toBe(iconPath)
  fireEvent.focus(row)
  expect(within(screen.getByRole('tooltip')).getByText('Antigravity')).toBeTruthy()
  expect(fetchSource).toHaveBeenCalledWith(expect.stringContaining('client_source=agy'), expect.anything())
})
