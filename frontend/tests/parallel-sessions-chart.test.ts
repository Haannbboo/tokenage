// @vitest-environment jsdom
import { createElement } from 'react'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ParallelSessionsChart, parallelPeak } from '../src/components/ParallelSessionsChart'

const point = (i: number, session_count: number) => ({ key: Date.UTC(2026, 10, 9, i) * 1000, period: `2026-11-09 ${String(i).padStart(2, '0')}:00`, start: '', end: '', session_count })
afterEach(cleanup)

it('reports the first peak', () => {
  const points = [0, 2, 5, 0, 1, 5, 3].map((n, i) => point(i, n))
  expect(parallelPeak(points)?.key).toBe(points[2].key)
  expect(parallelPeak([point(0, 0)])).toBeNull()
  expect(parallelPeak([])).toBeNull()
})

it('draws a read-only line with the peak marked without text', () => {
  const points = [0, 2, 5, 1].map((n, i) => point(i, n))
  const view = render(createElement(ParallelSessionsChart, { points, tz: 'UTC' }))
  const svg = view.container.querySelector('svg')!
  expect(view.container.querySelector('button, [onclick], [tabindex]')).toBeNull()
  expect(svg.getAttribute('aria-label')).toMatch(/peak 5 at once at/)
  expect(view.container.querySelector('h3')!.textContent).toBe('Parallel sessions')
  expect(svg.querySelector('.parallel-chart-peak-label')).toBeNull()
  expect([...svg.querySelectorAll('text')].map(el => el.textContent)).not.toContain('5')
  const peakLine = svg.querySelector('.parallel-chart-peak-line')!
  expect(peakLine.getAttribute('y1')).toBe(svg.querySelector('.parallel-chart-peak-dot')!.getAttribute('cy'))
  // Behind the series line.
  expect(peakLine.compareDocumentPosition(svg.querySelector('path')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(view.container.querySelectorAll('.parallel-chart-peak-dot')).toHaveLength(1)
  svg.getBoundingClientRect = () => ({ left: 0, width: 800 }) as DOMRect
  fireEvent.pointerMove(svg, { clientX: 799, pointerType: 'mouse' })
  expect(view.container.querySelector('.chart-tip')!.textContent).toMatch(/· 1 session$/)
  fireEvent.pointerLeave(svg, { pointerType: 'mouse' })
  expect(view.container.querySelector('.chart-tip')).toBeNull()
  // A tap picks a point and stays shown after the finger lifts.
  fireEvent.pointerDown(svg, { clientX: 0, pointerType: 'touch' })
  fireEvent.pointerLeave(svg, { pointerType: 'touch' })
  expect(view.container.querySelector('.chart-tip')!.textContent).toMatch(/· 0 sessions$/)
})

it('shows the empty state without points', () => {
  const view = render(createElement(ParallelSessionsChart, { points: [], tz: 'UTC' }))
  expect(view.container.querySelector('svg')).toBeNull()
  expect(view.container.textContent).toContain('No sessions yet.')
})
