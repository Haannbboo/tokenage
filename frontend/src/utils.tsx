import type { DailyUsage, PeriodGranularity } from './types'
import { zeroUsageRow } from './periodGaps'
import { getLang, LANG_LOCALES } from './i18n/index.ts'
import { getTheme, type Theme } from './theme'

const TIMEZONE_KEY = 'tokenage-timezone'

export const TIMEZONES: Record<string, string[]> = {
  'Americas': [
    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
    'America/Sao_Paulo', 'America/Argentina/Buenos_Aires', 'America/Toronto', 'America/Vancouver',
  ],
  'Europe': [
    'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
    'Europe/Amsterdam', 'Europe/Stockholm', 'Europe/Istanbul', 'Europe/Madrid',
  ],
  'Asia': [
    'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Asia/Singapore',
    'Asia/Hong_Kong', 'Asia/Taipei', 'Asia/Kolkata', 'Asia/Bangkok', 'Asia/Dubai',
  ],
  'Pacific': [
    'Australia/Sydney', 'Australia/Melbourne', 'Pacific/Auckland', 'Pacific/Honolulu',
  ],
  'Other': [
    'UTC',
  ],
}

export function getLocalTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

export function resolveTimezone(tz: string): string {
  return tz === 'auto' ? getLocalTimezone() : validateTimezone(tz)
}

function validateTimezone(tz: string): string {
  try {
    new Intl.DateTimeFormat(undefined, { timeZone: tz })
    return tz
  } catch {
    return getLocalTimezone()
  }
}

export function getSavedTimezone(): string {
  return localStorage.getItem(TIMEZONE_KEY) || 'auto'
}

export function saveTimezone(tz: string): void {
  localStorage.setItem(TIMEZONE_KEY, tz)
}

// The formatters follow the language the UI is written in, not the browser
// locale: a German browser would otherwise read 2,090 as 2.090.
const numberFormatters = new Map<string, Intl.NumberFormat>()
const compactFormatters = new Map<string, Intl.NumberFormat>()
const costFormatters = new Map<string, Intl.NumberFormat>()

function appLocale(): string {
  return LANG_LOCALES[getLang()]
}

function numberFormatter(): Intl.NumberFormat {
  const locale = appLocale()
  let fmt = numberFormatters.get(locale)
  if (!fmt) {
    fmt = new Intl.NumberFormat(locale)
    numberFormatters.set(locale, fmt)
  }
  return fmt
}

function compactFormatter(): Intl.NumberFormat {
  const locale = appLocale()
  let fmt = compactFormatters.get(locale)
  if (!fmt) {
    fmt = new Intl.NumberFormat(locale, {
      maximumFractionDigits: 1,
      notation: 'compact',
    })
    compactFormatters.set(locale, fmt)
  }
  return fmt
}

function getCostFormatter(maxDigits: number) {
  const locale = appLocale()
  const key = `${locale}:${maxDigits}`
  let fmt = costFormatters.get(key)
  if (!fmt) {
    fmt = new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: maxDigits,
    })
    costFormatters.set(key, fmt)
  }
  return fmt
}

export function value(input: number | null | undefined) {
  return input ?? 0
}

export function timeAgo(input: string | number): string {
  const date = typeof input === 'number' ? new Date(input / 1000) : new Date(input)
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function formatNumber(input: number | null | undefined) {
  return numberFormatter().format(value(input))
}

export function formatCompact(input: number | null | undefined) {
  return compactFormatter().format(value(input))
}

export function formatCost(input: number | null | undefined, maxDigits = 6) {
  const v = value(input)
  if (v === 0) return '$0.00'
  if (Math.abs(v) < 0.01) return `$${v.toFixed(Math.min(4, maxDigits))}`
  if (Math.abs(v) < 0.1) return `$${v.toFixed(Math.min(3, maxDigits))}`
  return getCostFormatter(maxDigits).format(v)
}

/** Currency with a fixed number of decimals, including trailing zeros. */
export function formatCostFixed(input: number | null | undefined, digits = 3) {
  const locale = appLocale()
  const key = `${locale}:fixed:${digits}`
  let fmt = costFormatters.get(key)
  if (!fmt) {
    fmt = new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })
    costFormatters.set(key, fmt)
  }
  return fmt.format(value(input))
}

export function formatRate(input: number | null | undefined) {
  if (input === null || input === undefined) return ''
  return `$${input.toFixed(3)}/1M`
}

export function formatLatency(input: number | null | undefined) {
  if (input == null) return '—'
  const latency = value(input)
  return latency >= 1000 ? `${(latency / 1000).toFixed(2)}s` : `${Math.round(latency)}ms`
}

export function formatSpeed(tokens: number | null | undefined, latencyMs: number | null | undefined): string {
  const t = value(tokens)
  const ms = value(latencyMs)
  if (t <= 0 || ms <= 0) return ''
  const tps = t / (ms / 1000)
  return tps >= 100 ? `${Math.round(tps)} tok/s` : `${tps.toFixed(1)} tok/s`
}

export function netThroughput(
  completionTokens: number | null | undefined,
  latencyMs: number | null | undefined,
): number {
  const ms = value(latencyMs)
  return ms > 0 ? (value(completionTokens) * 1000) / ms : 0
}

// cache_creation_tokens is a disjoint bucket on top of prompt_tokens (Anthropic
// semantics), so it belongs in the denominator of a true cache hit rate.
// A bucket without any input reads as 0%.
export function cacheHitRate(
  cached: number | null | undefined,
  prompt: number | null | undefined,
  cacheCreation: number | null | undefined,
): number {
  const totalInput = value(prompt) + value(cacheCreation)
  return totalInput > 0 ? (value(cached) / totalInput) * 100 : 0
}

type DurationFormatOptions = {
  secondsFractionDigits?: number
}

function formatDurationSeconds(seconds: number, options: DurationFormatOptions): string {
  if (options.secondsFractionDigits === undefined) return `${seconds}s`
  return `${seconds.toFixed(options.secondsFractionDigits)}s`
}

export function formatDuration(seconds: number | null | undefined, options: DurationFormatOptions = {}): string {
  const s = value(seconds)
  if (s < 60) return formatDurationSeconds(s, options)
  const m = Math.floor(s / 60)
  const rem = s % 60
  if (m < 60) return rem > 0 ? `${m}m ${formatDurationSeconds(rem, options)}` : `${m}m`
  const h = Math.floor(m / 60)
  const remM = m % 60
  return remM > 0 ? `${h}h ${remM}m` : `${h}h`
}

export function formatThroughput(val: number | null | undefined): string {
  if (val == null || val === 0) return '—'
  return `${val.toFixed(1)} t/s`
}

export function formatTime(input: string | number, tz?: string) {
  const date = typeof input === 'number' ? new Date(input / 1000) : new Date(input)
  if (Number.isNaN(date.valueOf())) return String(input)
  const options: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  }
  if (tz) options.timeZone = tz
  return new Intl.DateTimeFormat(undefined, options).format(date)
}

function tzDateParts(date: Date, tz?: string): { year: number; month: number; day: number; hour: number; minute: number } {
  if (!tz) {
    return {
      year: date.getFullYear(), month: date.getMonth() + 1, day: date.getDate(),
      hour: date.getHours(), minute: date.getMinutes(),
    }
  }
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', hour12: false,
  }).formatToParts(date)
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value || '0', 10)
  const hour = get('hour') % 24
  return { year: get('year'), month: get('month'), day: get('day'), hour, minute: get('minute') }
}

/**
 * The wall-clock time of `now` in the display timezone, in the same clock the
 * period labels use. "3 hours ago" is only true against this, not against the
 * browser's clock.
 */
export function wallClockMs(now: Date, tz?: string): number {
  const p = tzDateParts(now, tz)
  return Date.UTC(p.year, p.month - 1, p.day, p.hour)
}

export function getTimezoneOffset(tz?: string): string {
  if (!tz) {
    const offset = -new Date().getTimezoneOffset();
    const absOffset = Math.abs(offset);
    const hours = Math.floor(absOffset / 60);
    const mins = absOffset % 60;
    const sign = offset >= 0 ? '+' : '-';
    return `${sign}${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}`;
  }
  const ref = new Date(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), new Date().getUTCDate(), 12, 0, 0))
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric', hour12: false,
  }).formatToParts(ref)
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value || '0', 10)
  // Build a UTC timestamp from the timezone-local parts.
  // Note: the local date may differ from ref's UTC date (e.g. UTC+7 at 2am UTC = 9pm previous day).
  // Normalize the difference to [-720, +840] minutes to handle day-boundary crossings.
  const localAsUtc = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour') % 24, get('minute'), get('second'))
  let diffMin = Math.round((localAsUtc - ref.getTime()) / 60_000)
  if (diffMin > 840) diffMin -= 1440
  if (diffMin < -720) diffMin += 1440
  const sign = diffMin >= 0 ? '+' : '-'
  const absMin = Math.abs(diffMin)
  return `${sign}${String(Math.floor(absMin / 60)).padStart(2, '0')}:${String(absMin % 60).padStart(2, '0')}`
}


export function fillGaps(data: DailyUsage[], granularity: PeriodGranularity, periodCount: number, tz?: string): DailyUsage[] {
  const effectiveTz = tz
  const zeroRow = zeroUsageRow

  const map = new Map(data.map(d => [d.period, d]))
  const result: DailyUsage[] = []

  const now = new Date()

  if (granularity === 'hour') {
    // Step back from the current instant by (periodCount-1) hours
    const startMs = now.getTime() - (periodCount - 1) * 3600_000
    for (let i = 0; i < periodCount; i++) {
      const d = new Date(startMs + i * 3600_000)
      const p = tzDateParts(d, effectiveTz)
      const key = `${p.year}-${String(p.month).padStart(2, '0')}-${String(p.day).padStart(2, '0')} ${String(p.hour).padStart(2, '0')}:00`
      result.push(map.get(key) ?? zeroRow(key))
    }
  } else if (granularity === '15m') {
    // Step back from the current quarter hour by (periodCount-1) quarter hours
    const quarterMs = 900_000
    const anchor = Math.floor(now.getTime() / quarterMs) * quarterMs
    const startMs = anchor - (periodCount - 1) * quarterMs
    for (let i = 0; i < periodCount; i++) {
      const d = new Date(startMs + i * quarterMs)
      const p = tzDateParts(d, effectiveTz)
      const key = `${p.year}-${String(p.month).padStart(2, '0')}-${String(p.day).padStart(2, '0')} ${String(p.hour).padStart(2, '0')}:${String(p.minute).padStart(2, '0')}`
      result.push(map.get(key) ?? zeroRow(key))
    }
  } else {
    // Step back from the current instant by (periodCount-1) days
    const startMs = now.getTime() - (periodCount - 1) * 86400_000
    for (let i = 0; i < periodCount; i++) {
      const d = new Date(startMs + i * 86400_000)
      const p = tzDateParts(d, effectiveTz)
      const key = `${p.year}-${String(p.month).padStart(2, '0')}-${String(p.day).padStart(2, '0')}`
      result.push(map.get(key) ?? zeroRow(key))
    }
  }

  return result
}


// Calm categorical palette (index.css --series-*); green first.
export const PALETTE = Array.from({ length: 8 }, (_, i) => `var(--series-${i + 1})`)

// ─── Identity colours: dimmed brand hues (index.css --hue-*) for small marks ─

const hue = (name: string) => `var(--hue-${name})`

/** A stable palette colour for a name with no identity colour of its own. */
export function nameColor(name: string) {
  let h = 0
  for (const ch of name.toLowerCase()) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return PALETTE[h % PALETTE.length]
}

/** First keyword contained in the name wins; a key ending in '/' must be a prefix. */
function keywordHue(name: string, table: [string, string][]) {
  const n = name.toLowerCase()
  const match = table.find(([key]) => key.endsWith('/') ? n.startsWith(key) : n.includes(key))
  return match ? hue(match[1]) : null
}

const FAMILY_HUES: Record<string, string> = {
  hunyuan: 'royal', openai: 'slate', claude: 'orange', gemini: 'blue', minimax: 'rose', xiaomi: 'sand',
  inclusionai: 'indigo', poolside: 'amber', deepseek: 'royal', doubao: 'cyan', openrouter: 'indigo', glm: 'royal',
  stepfun: 'sky', nvidia: 'lime', qwen: 'violet', cohere: 'teal', dots: 'teal', stealth: 'grey', meta: 'royal',
  grok: 'ink', longcat: 'green', commandcode: 'grey',
}

export function modelColor(model: string) {
  const id = FAMILY_HUES[modelFamily(model).id]
  return id ? hue(id) : nameColor(model)
}

const PROVIDER_HUES: [string, string][] = [
  ['tencent/', 'royal'], ['anthropic', 'orange'], ['openai', 'slate'], ['google', 'blue'], ['minimax', 'rose'],
  ['xiaomi', 'sand'], ['openrouter', 'indigo'], ['opencode', 'green'], ['inclusionai', 'indigo'], ['poolside', 'amber'],
  ['deepseek', 'royal'], ['doubao', 'cyan'], ['seed', 'cyan'], ['z-ai', 'royal'], ['stepfun', 'sky'], ['volce', 'sky'],
  ['nvidia', 'lime'], ['cohere', 'teal'], ['qwen', 'violet'], ['dots', 'teal'],
]

export function providerColor(provider: string) {
  return keywordHue(provider, PROVIDER_HUES) ?? modelColor(provider)
}

const SOURCE_HUES: [string, string][] = [
  ['claude', 'orange'], ['codex', 'slate'], ['gemini', 'blue'], ['opencode', 'green'], ['open-code', 'green'],
  ['kilo', 'cyan'], ['hermes', 'purple'], ['proxy', 'violet'],
]

export function sourceColor(source: string) {
  return keywordHue(source, SOURCE_HUES) ?? nameColor(source)
}

const TOOL_HUES: Record<string, string> = {
  bash: 'blue', shell: 'blue', exec_command: 'sky', exec: 'orange', read: 'green', edit: 'amber', apply_patch: 'amber',
  write: 'red', grep: 'violet', glob: 'cyan', list: 'cyan', task: 'indigo', lsp: 'indigo', webfetch: 'pink',
  websearch: 'purple', skill: 'teal', question: 'violet', todowrite: 'yellow', mcp_tool: 'rose',
}

export function toolColor(tool: string) {
  const id = TOOL_HUES[tool.toLowerCase()]
  return id ? hue(id) : nameColor(tool)
}

/**
 * One colour per key, in order. A key whose colour is already taken (two
 * Claude models) gets a lighter, then a deeper, shade of the same hue.
 */
export function distinctColors(keys: string[], colorOf: (key: string) => string): Map<string, string> {
  const taken = new Map<string, number>()
  return new Map(keys.map(key => {
    const color = colorOf(key)
    const n = taken.get(color) ?? 0
    taken.set(color, n + 1)
    if (n === 0) return [key, color]
    const toward = n % 2 ? 'var(--card-bg)' : 'var(--text-primary)'
    return [key, `color-mix(in oklab, ${color} ${Math.max(45, 100 - 25 * Math.ceil(n / 2))}%, ${toward})`]
  }))
}

export function getProviderColor(provider: string, providerColors: Record<string, string>): string {
  return providerColors[provider] || 'var(--chart-2)';
}

export function formatModelName(model: string): string {
  if (model.toLowerCase() === 'stealth/ox-alpha') return 'Ox Alpha'
  if (model.toLowerCase() === 'union-alpha') return 'Union Alpha'
  return model
}

export function formatProviderName(provider: string): string {
  const name = provider.toLowerCase()
  if (name.startsWith('minimax-') || name.split('.').some(part => part === 'minimax' || part === 'minimaxi')) return 'MiniMax'
  return provider
}

const ICON_STYLE = { width: 14, height: 14, display: 'block', objectFit: 'contain' as const }

export function modelFamily(model: string): { id: string; label: string } {
  const m = model.toLowerCase()
  if (m.startsWith('tencent/') || m.startsWith('hy3')) return { id: 'hunyuan', label: 'Hunyuan' }
  if (m.includes('gpt') || m.includes('codex')) return { id: 'openai', label: 'GPT' }
  if (m.includes('claude')) return { id: 'claude', label: 'Claude' }
  if (m.includes('gemini') || m.includes('google') || m.includes('gemma')) return { id: 'gemini', label: 'Gemini' }
  if (m.includes('minimax') || m.includes('mimimax')) return { id: 'minimax', label: 'MiniMax' }
  if (m.includes('mimo') || m.includes('xiaomi')) return { id: 'xiaomi', label: 'Xiaomi MiMo' }
  if (m.includes('inclusionai') || m.includes('ling')) return { id: 'inclusionai', label: 'InclusionAI' }
  if (m.includes('poolside')) return { id: 'poolside', label: 'Poolside' }
  if (m.includes('deepseek')) return { id: 'deepseek', label: 'DeepSeek' }
  if (m.includes('doubao') || m.includes('seed')) return { id: 'doubao', label: 'Doubao' }
  if (m.includes('openrouter')) return { id: 'openrouter', label: 'OpenRouter' }
  if (m.startsWith('z-ai/') || m.includes('glm')) return { id: 'glm', label: 'GLM/Z.ai' }
  if (m.includes('stepfun') || m.includes('step-')) return { id: 'stepfun', label: 'StepFun' }
  if (m.startsWith('nvidia/') || m.includes('nemotron')) return { id: 'nvidia', label: 'NVIDIA' }
  if (m.includes('qwen')) return { id: 'qwen', label: 'Qwen' }
  if (m.startsWith('cohere/')) return { id: 'cohere', label: 'Cohere' }
  if (m.includes('dots')) return { id: 'dots', label: 'Dots' }
  if (m.startsWith('stealth') || m.endsWith('-alpha')) return { id: 'stealth', label: 'Stealth' }
  if (m.includes('muse') || m.includes('meta/')) return { id: 'meta', label: 'Meta' }
  if (m.startsWith('x-ai/') || m.includes('grok')) return { id: 'grok', label: 'Grok' }
  if (m.includes('longcat')) return { id: 'longcat', label: 'LongCat' }
  if (m.replace(/[-_\s]/g, '').includes('commandcode')) return { id: 'commandcode', label: 'Command Code' }
  return { id: 'other', label: 'Other' }
}

const MODEL_FAMILY_ICONS: Record<string, string> = {
  hunyuan: '/models/hunyuan-color.svg',
  openai: '/models/openai.svg',
  claude: '/models/claude-ai-icon.svg',
  gemini: '/models/google-gemini-icon.svg',
  minimax: '/models/minimax-color.svg',
  xiaomi: '/models/xiaomi.svg',
  inclusionai: '/models/inclusionai.png',
  poolside: '/models/poolside.svg',
  deepseek: '/models/deepseek.svg',
  doubao: '/models/doubao.svg',
  openrouter: '/models/openrouter.svg',
  glm: '/models/z-ai.svg',
  stepfun: '/models/stepfun-color.svg',
  nvidia: '/models/nvidia.svg',
  qwen: '/models/qwen-color.svg',
  cohere: '/models/cohere.svg',
  dots: '/models/dots-studio.png',
  stealth: '/models/stealth.svg',
  meta: '/models/meta.svg',
  grok: '/models/grok.svg',
  longcat: '/models/longcat-color.svg',
  commandcode: '/models/commandcode.svg',
}

export function getModelIcon(model: string, theme: Theme = getTheme()) {
  const id = modelFamily(model).id
  const darkIcons: Record<string, string> = { openai: '/models/openai-light.svg', openrouter: '/models/openrouter-dark.svg', grok: '/models/grok-dark.svg', commandcode: '/models/commandcode-dark.svg' }
  const src = (theme === 'dark' && darkIcons[id]) || MODEL_FAMILY_ICONS[id]
  return src ? <img src={src} alt="" style={ICON_STYLE} /> : null
}

export function getProviderIcon(provider: string, theme: Theme = getTheme()) {
  const p = provider.toLowerCase()
  const style = ICON_STYLE
  const dark = theme === 'dark'
  if (p.startsWith('tencent/')) return <img src="/models/hunyuan-color.svg" alt="" style={style} />
  if (p.includes('anthropic')) return <img src="/models/claude-ai-icon.svg" alt="" style={style} />
  if (p.includes('openai')) return <img src={dark ? '/models/openai-light.svg' : '/models/openai.svg'} alt="" style={style} />
  if (p.includes('google')) return <img src="/models/google-gemini-icon.svg" alt="" style={style} />
  if (p.includes('minimax')) return <img src="/models/minimax-color.svg" alt="" style={style} />
  if (p.includes('xiaomi')) return <img src="/models/xiaomi.svg" alt="" style={style} />
  if (p.includes('openrouter')) return <img src={dark ? '/models/openrouter-dark.svg' : '/models/openrouter.svg'} alt="" style={style} />
  if (p.includes('opencode')) return <img src="/models/opencode.svg" alt="" style={style} />
  if (p.includes('inclusionai') || p.includes('ling')) return <img src="/models/inclusionai.png" alt="" style={style} />
  if (p.includes('poolside')) return <img src="/models/poolside.svg" alt="" style={style} />
  if (p.includes('deepseek')) return <img src="/models/deepseek.svg" alt="" style={style} />
  if (p.includes('doubao') || p.includes('seed')) return <img src="/models/doubao.svg" alt="" style={style} />
  if (p.includes('z-ai')) return <img src="/models/z-ai.svg" alt="" style={style} />
  if (p.includes('stepfun')) return <img src="/models/stepfun-color.svg" alt="" style={style} />
  if (p.includes('volce')) return <img src="/models/volcengine.svg" alt="" style={style} />
  if (p.includes('nvidia')) return <img src="/models/nvidia.svg" alt="" style={style} />
  if (p.includes('cohere')) return <img src="/models/cohere.svg" alt="" style={style} />
  if (p.includes('qwen')) return <img src="/models/qwen-color.svg" alt="" style={style} />
  if (p.includes('dots')) return <img src="/models/dots-studio.png" alt="" style={style} />
  if (p.replace(/[-_\s]/g, '').includes('commandcode')) return <img src={dark ? '/models/commandcode-dark.svg' : '/models/commandcode.svg'} alt="" style={style} />
  return null
}

export function getSourceIcon(source: string, theme: Theme = getTheme()) {
  const s = source.toLowerCase()
  const style = ICON_STYLE
  const dark = theme === 'dark'
  if (s.includes('hermes')) return <img src={dark ? '/models/hermesagent-dark.png' : '/models/hermesagent.svg'} alt="" style={style} />
  if (s.includes('codex')) return <img src="/models/codex-color.svg" alt="" style={style} />
  if (s.includes('opencode') || s.includes('open-code')) return <img src="/models/opencode.svg" alt="" style={style} />
  if (s.includes('claude')) return <img src="/models/claude-ai-icon.svg" alt="" style={style} />
  if (s.includes('kilo')) return <img src={dark ? '/models/kilocode-dark.png' : '/models/kilocode.png'} alt="" style={style} />
  return null
}

export function shortSessionId(id: string) {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id
}

export function sessionAgentName(source: string | null | undefined) {
  const raw = source || 'unknown'
  return raw
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ') || 'Unknown'
}

export function getAgentDisplayName(name: string) {
  const normalized = name.toLowerCase()
  if (normalized.includes('vectorengine') || normalized.includes('claude')) return 'Claude Code'
  if (normalized.includes('codesonline') || normalized.includes('codex')) return 'Codex'
  if (normalized.includes('opencode')) return 'OpenCode'
  if (normalized.includes('kilo')) return 'Kilo Code'
  return name
}

export function getSetupAgentKey(name: string) {
  const normalized = name.toLowerCase()
  if (normalized.includes('vectorengine') || normalized.includes('claude')) return 'claude'
  if (normalized.includes('codesonline') || normalized.includes('codex')) return 'codex'
  return normalized
}

export function ToolBadge({ name, count, style }: { name: string; count?: number; style?: React.CSSProperties }) {
  return (
    <span className="entity-badge mono tinted" style={{ '--tint': toolColor(name), ...style } as React.CSSProperties}>
      {name}{count != null ? `×${count}` : ''}
    </span>
  )
}
