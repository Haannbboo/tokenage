import { getModelBadgeBackgroundColor } from '../model-badge'
import type { Theme } from '../theme'

/** The card the chart is drawn on, per theme. */
const CARD_BG: Record<Theme, string> = { light: '#ffffff', dark: '#1e293b' }

/** The lightness a series colour aims for, per theme. */
const TARGET_LIGHTNESS: Record<Theme, number> = { light: 0.50, dark: 0.60 }

/** The saturation a brand with a hue of its own gets. */
const VIVID_SATURATION = 0.95

/** The saturation a grey brand keeps of its badge hue. */
const GREY_SATURATION = 0.15

/** Below this chroma a badge has no hue of its own: it is plain grey. */
const NO_CHROMA = 0.05

/** Below this chroma a badge reads as grey rather than as a brand colour. */
const GREY_CHROMA = 0.16

/** The lowest contrast a series may have against its card. */
const MIN_CONTRAST = 2

/** How close two colours may get before the later one steps aside. */
const MIN_DELTA_E = 20

/** How far one step moves the lightness. */
const LIGHTNESS_STEP = 0.04

/** How many steps to try before settling for the closest fit. */
const MAX_STEPS = 14

type Hsl = { h: number; s: number; l: number }

function rgbOf(hex: string): number[] {
  const n = parseInt(hex.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** A colour as hue, saturation and lightness. */
function toHsl(hex: string): Hsl {
  const [r, g, b] = rgbOf(hex).map(c => c / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const d = max - min
  const l = (max + min) / 2
  if (!d) return { h: 0, s: 0, l }
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  const raw = max === r ? 60 * (((g - b) / d) % 6) : max === g ? 60 * ((b - r) / d + 2) : 60 * ((r - g) / d + 4)
  return { h: (raw + 360) % 360, s, l }
}

/** A hue, saturation and lightness back as a colour. */
function toHex({ h, s, l }: Hsl): string {
  const c = (1 - Math.abs(2 * l - 1)) * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = l - c / 2
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x]
  const part = (v: number) => Math.max(0, Math.min(255, Math.round((v + m) * 255)))
  return `#${((part(r) << 16) | (part(g) << 8) | part(b)).toString(16).padStart(6, '0')}`
}

/** How much colour a badge has, from 0 (plain grey) to 1 (pure hue). */
function chromaOf(hex: string): number {
  const [r, g, b] = rgbOf(hex)
  return (Math.max(r, g, b) - Math.min(r, g, b)) / 255
}

/** The colour of a model: its badge background (alpha dropped), pulled into one shape. */
function baseColor(model: string, theme: Theme): Hsl {
  const badge = getModelBadgeBackgroundColor(model, theme).slice(0, 7)
  const { h, s } = toHsl(badge)
  const chroma = chromaOf(badge)
  const l = TARGET_LIGHTNESS[theme]
  // A badge without any chroma has no hue to keep: it is plain grey.
  if (chroma < NO_CHROMA) return { h: 0, s: 0, l }
  // A badge with little chroma reads as grey: keep its hue, not its colour.
  if (chroma < GREY_CHROMA) return { h, s: Math.min(s, GREY_SATURATION), l }
  // A brand with a hue of its own keeps the hue and gets a vivid saturation,
  // so a pale yellow turns gold rather than olive.
  return { h, s: VIVID_SATURATION, l }
}

/** Moves the lightness away from the card until the colour can be read on it. */
function readable(base: Hsl, theme: Theme): Hsl {
  const card = CARD_BG[theme]
  const away = theme === 'light' ? -LIGHTNESS_STEP : LIGHTNESS_STEP
  let l = base.l
  while (l > 0.02 && l < 0.98 && contrastRatio(toHex({ ...base, l }), card) < MIN_CONTRAST) l += away
  return { ...base, l }
}

/** The colour of a model with no brand of its own. */
export const TREND_NEUTRAL: Record<Theme, string> = {
  light: toHex({ h: 0, s: 0, l: TARGET_LIGHTNESS.light }),
  dark: toHex({ h: 0, s: 0, l: TARGET_LIGHTNESS.dark }),
}

/**
 * Colours for the series drawn in the chart, in legend order (biggest first).
 * A series is one model, or one family of models, and gets the colour of the
 * model it stands for. One that is too close to a series already drawn steps
 * aside in lightness, away from the card first.
 */
export function trendSeriesColors(series: { key: string; model: string }[], theme: Theme): Map<string, string> {
  const bases = series.map(s => readable(baseColor(s.model, theme), theme))
  const chosen: string[] = []
  for (const base of bases) chosen.push(distinct(base, chosen, theme))
  return new Map(series.map((s, i) => [s.key, chosen[i]]))
}

/**
 * The colour of one series: its own colour, unless that is too close to a
 * colour already on the chart. Then it steps in lightness, away from the
 * card first, until it is distinct.
 */
function distinct(base: Hsl, taken: string[], theme: Theme): string {
  const card = CARD_BG[theme]
  const away = theme === 'light' ? -1 : 1
  const closest = (color: string) => (taken.length ? Math.min(...taken.map(t => deltaE(t, color))) : Infinity)
  let best = toHex(base)
  let bestDistance = closest(best)
  if (bestDistance >= MIN_DELTA_E) return best
  for (const direction of [away, -away]) {
    for (let step = 1; step <= MAX_STEPS; step++) {
      const l = base.l + direction * step * LIGHTNESS_STEP
      if (l < 0.02 || l > 0.98) continue
      const color = toHex({ ...base, l })
      if (contrastRatio(color, card) < MIN_CONTRAST) continue
      const distance = closest(color)
      if (distance >= MIN_DELTA_E) return color
      if (distance > bestDistance) {
        bestDistance = distance
        best = color
      }
    }
  }
  return best
}

function linearRgb(hex: string): number[] {
  return rgbOf(hex).map(c => {
    const s = c / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
}

/** WCAG relative luminance of a colour. */
export function luminance(hex: string): number {
  const [r, g, b] = linearRgb(hex)
  return 0.2126729 * r + 0.7151522 * g + 0.072175 * b
}

/** WCAG contrast ratio between two colours. */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** CIE76 colour difference between two colours. */
export function deltaE(a: string, b: string): number {
  const lab = (hex: string) => {
    const [r, g, b] = linearRgb(hex)
    const y = 0.2126729 * r + 0.7151522 * g + 0.072175 * b
    const f = (n: number) => (n > 0.008856451679 ? Math.cbrt(n) : n / 0.12841855 + 4 / 29)
    const x = f((0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047)
    const z = f((0.0193339 * r + 0.119192 * g + 0.9503041 * b) / 1.08883)
    const fy = f(y)
    return [116 * fy - 16, 500 * (x - fy), 200 * (fy - z)]
  }
  const [l1, a1, b1] = lab(a)
  const [l2, a2, b2] = lab(b)
  return Math.hypot(l1 - l2, a1 - a2, b1 - b2)
}
