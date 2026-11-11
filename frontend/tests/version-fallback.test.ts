// @vitest-environment jsdom
// @vitest-environment-options {"url":"https://tokens.example.com"}
import { createElement } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { EmptyGuide } from '../src/components/EmptyGuide'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const open = () => render(createElement(EmptyGuide, {
  verificationResult: null, onViewLogs: vi.fn(), onDone: vi.fn(),
}))

it('retries version twice then shows the GitHub installer and origin hint', async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ ok: false }).mockRejectedValueOnce(new Error('offline'))
  vi.stubGlobal('fetch', fetch)
  open()
  expect(screen.queryByText(/curl -fsSL/)).toBeNull()
  expect(await screen.findByText('curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | sh')).toBeTruthy()
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(screen.getByText('https://tokens.example.com')).toBeTruthy()
})

it('uses the served installer if a version retry succeeds', async () => {
  const fetch = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({
    ok: true, json: async () => ({ version: '1', installer_available: true, server_url: 'https://api.example.com' }),
  })
  vi.stubGlobal('fetch', fetch)
  open()
  expect(await screen.findByText('curl -fsSL https://api.example.com/install.sh | sh')).toBeTruthy()
  expect(fetch).toHaveBeenCalledTimes(2)
})
