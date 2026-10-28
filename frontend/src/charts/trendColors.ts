import { getModelColor } from '../model-badge'
import type { Theme } from '../theme'

export function trendBrandColor(model: string, fallback: string): string {
  const m = model.toLowerCase()
  if (m.includes('opus')) return '#9a5b3c'
  if (m.includes('sonnet')) return '#c98a63'
  if (m.includes('minimax')) return '#ef4444'
  return fallback
}

export function vibrantColor(hex: string, boost = 1.4, lift = 1.06): string {
  const n = parseInt(hex.slice(1), 16)
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  const gray = 0.299 * r + 0.587 * g + 0.114 * b
  const mix = (c: number) => Math.round(Math.max(0, Math.min(255, (gray + (c - gray) * boost) * lift)))
  return `#${((mix(r) << 16) | (mix(g) << 8) | mix(b)).toString(16).padStart(6, '0')}`
}

/**
 * A distinguishable variant of the same colour, for models that share a base
 * (two MiniMax spellings, two Claude tiers). Stays in the base hue so a brand
 * still reads as one brand, and moves away from the background: lighter on a
 * dark theme, darker on a light one.
 */
export function variantColor(hex: string, step: number, theme: 'light' | 'dark' = 'light'): string {
  const n = parseInt(hex.slice(1), 16)
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  const amount = Math.min(0.45, 0.24 * step)
  const mix = (c: number) =>
    Math.round(theme === 'dark' ? c + (255 - c) * amount : c * (1 - amount))
  return `#${((mix(r) << 16) | (mix(g) << 8) | mix(b)).toString(16).padStart(6, '0')}`
}

/**
 * Colour per model. Models that resolve to the same base (two spellings of
 * MiniMax, two Claude tiers) get shades of that one hue so the brand reads as
 * a single brand, instead of the unrelated badge palette.
 */
export function trendModelColors(models: string[], theme: Theme): Map<string, string> {
  const map = new Map<string, string>()
  const seen = new Map<string, number>()
  for (const model of models) {
    const base = vibrantColor(trendBrandColor(model, getModelColor(model, theme)))
    const n = seen.get(base) ?? 0
    seen.set(base, n + 1)
    map.set(model, n === 0 ? base : variantColor(base, n, theme))
  }
  return map
}
