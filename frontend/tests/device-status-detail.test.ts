// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { DeviceStatusDetail } from '../src/components/DeviceStatusDetail'

afterEach(cleanup)
it('pairs VectorEngine detection with Claude health and deduplicates aliases', () => {
  render(createElement(DeviceStatusDetail, { status: {
    detected: { VectorEngine: { found: true }, 'Claude Code': { found: true } },
    agents: { claude: { status: 'ready', configured: true, endpoint_matches: true } },
  } }))
  expect(screen.getAllByText('Claude Code')).toHaveLength(1)
  expect(screen.getAllByText('Ready')).toHaveLength(1)
})
