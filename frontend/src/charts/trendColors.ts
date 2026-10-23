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
