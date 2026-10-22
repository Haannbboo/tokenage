import type { DateRangeOption, DailyUsage } from './types'
import type { Lang } from './i18n/index.ts'
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

export const numberFormatter = new Intl.NumberFormat()
export const compactFormatter = new Intl.NumberFormat(undefined, {
  maximumFractionDigits: 1,
  notation: 'compact',
})
const costFormatters = new Map<number, Intl.NumberFormat>()
function getCostFormatter(maxDigits: number) {
  let fmt = costFormatters.get(maxDigits)
  if (!fmt) {
    fmt = new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: maxDigits,
    })
    costFormatters.set(maxDigits, fmt)
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
  return numberFormatter.format(value(input))
}

export function formatCompact(input: number | null | undefined) {
  return compactFormatter.format(value(input))
}

export function formatCost(input: number | null | undefined, maxDigits = 6) {
  const v = value(input)
  if (v === 0) return '$0.00'
  if (Math.abs(v) < 0.01) return `$${v.toFixed(Math.min(4, maxDigits))}`
  if (Math.abs(v) < 0.1) return `$${v.toFixed(Math.min(3, maxDigits))}`
  return getCostFormatter(maxDigits).format(v)
}

export function formatRate(input: number | null | undefined) {
  if (input === null || input === undefined) return ''
  return `$${input.toFixed(3)}/1M`
}

export function formatLatency(input: number | null | undefined) {
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
// Returns null when there is no input at all: there is no hit rate to report.
export function cacheHitRate(
  cached: number | null | undefined,
  prompt: number | null | undefined,
  cacheCreation: number | null | undefined,
): number | null {
  const totalInput = value(prompt) + value(cacheCreation)
  return totalInput > 0 ? (value(cached) / totalInput) * 100 : null
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

// The custom range is datetime-local (no zone); the API reads naive bounds as UTC.
export function customBound(local: string): string {
  return local && new Date(local).toISOString()
}

export function getSinceDate(option: DateRangeOption): string | null {
  if (option === 'custom' || option === 'all') return null
  if (option === '24h') return new Date(Date.now() - 24 * 3600_000).toISOString()
  if (option === '7d') return new Date(Date.now() - 7 * 86400_000).toISOString()
  if (option === '30d') return new Date(Date.now() - 30 * 86400_000).toISOString()
  return null
}

function tzDateParts(date: Date, tz?: string): { year: number; month: number; day: number; hour: number } {
  if (!tz) {
    return { year: date.getFullYear(), month: date.getMonth() + 1, day: date.getDate(), hour: date.getHours() }
  }
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', hour12: false,
  }).formatToParts(date)
  const get = (type: string) => parseInt(parts.find(p => p.type === type)?.value || '0', 10)
  const hour = get('hour') % 24
  return { year: get('year'), month: get('month'), day: get('day'), hour }
}

export function fillGaps(data: DailyUsage[], granularity: 'hour' | 'day', periodCount: number, tz?: string): DailyUsage[] {
  const effectiveTz = tz
  const zeroRow = (period: string): DailyUsage => ({
    period, requests: 0, prompt_tokens: 0, completion_tokens: 0,
    cached_tokens: 0, cache_creation_tokens: 0, total_tokens: 0, input_cost_usd: 0,
    output_cost_usd: 0, total_cost_usd: 0, avg_latency_ms: 0,
    latency_sum_ms: 0,
    avg_throughput: 0,
    successful_requests: 0, failed_requests: 0,
    status_429: 0, status_4xx: 0, status_5xx: 0, status_unknown: 0,
  })

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
  // Normalize the difference to [-720, +720] minutes to handle day-boundary crossings.
  const localAsUtc = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour') % 24, get('minute'), get('second'))
  let diffMin = Math.round((localAsUtc - ref.getTime()) / 60_000)
  if (diffMin > 720) diffMin -= 1440
  if (diffMin < -720) diffMin += 1440
  const sign = diffMin >= 0 ? '+' : '-'
  const absMin = Math.abs(diffMin)
  return `${sign}${String(Math.floor(absMin / 60)).padStart(2, '0')}:${String(absMin % 60).padStart(2, '0')}`
}

export const PALETTE = [
  '#00b578', // Emerald/Teal
  '#3b82f6', // Blue
  '#f59e0b', // Amber
  '#06b6d4', // Cyan
  '#84cc16', // Lime
  '#f43f5e', // Rose
  '#8b5cf6', // Violet
  '#6366f1', // Indigo
  '#a855f7', // Purple
  '#ec4899', // Pink
];

export const FIXED_PROVIDER_COLORS: Record<string, string> = {
  'anthropic': '#cc7c5e',
  'google': '#528af2',
  'openai': '#94a3b8',
  'openrouter': '#6366f1',
  'stepfun': '#01A9FF',
};

export function getProviderColor(provider: string, providerColors: Record<string, string>): string {
  return providerColors[provider] || '#94a3b8';
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

export function getModelIcon(model: string, theme: Theme = getTheme()) {
  const m = model.toLowerCase()
  const style = ICON_STYLE
  const dark = theme === 'dark'
  if (m.startsWith('tencent/') || m.startsWith('hy3')) return <img src="/models/hunyuan-color.svg" alt="" style={style} />
  if (m.includes('gpt') || m.includes('codex')) return <img src={dark ? '/models/openai-light.svg' : '/models/openai.svg'} alt="" style={style} />
  if (m.includes('claude')) return <img src="/models/claude-ai-icon.svg" alt="" style={style} />
  if (m.includes('gemini') || m.includes('google') || m.includes('gemma')) return <img src="/models/google-gemini-icon.svg" alt="" style={style} />
  if (m.includes('minimax') || m.includes('mimimax')) return <img src="/models/minimax-color.svg" alt="" style={style} />
  if (m.includes('mimo') || m.includes('xiaomi')) return <img src="/models/xiaomi.svg" alt="" style={style} />
  if (m.includes('inclusionai') || m.includes('ling')) return <img src="/models/inclusionai.png" alt="" style={style} />
  if (m.includes('poolside')) return <img src="/models/poolside.svg" alt="" style={style} />
  if (m.includes('deepseek')) return <img src="/models/deepseek.svg" alt="" style={style} />
  if (m.includes('doubao') || m.includes('seed')) return <img src="/models/doubao.svg" alt="" style={style} />
  if (m.includes('openrouter')) return <img src={dark ? '/models/openrouter-dark.svg' : '/models/openrouter.svg'} alt="" style={style} />
  if (m.startsWith('z-ai/') || m.includes('glm')) return <img src="/models/z-ai.svg" alt="" style={style} />
  if (m.includes('stepfun') || m.includes('step-')) return <img src="/models/stepfun-color.svg" alt="" style={style} />
  if (m.startsWith('nvidia/') || m.includes('nemotron')) return <img src="/models/nvidia.svg" alt="" style={style} />
  if (m.includes('qwen')) return <img src="/models/qwen-color.svg" alt="" style={style} />
  if (m.startsWith('cohere/')) return <img src="/models/cohere.svg" alt="" style={style} />
  if (m.includes('dots')) return <img src="/models/dots-studio.png" alt="" style={style} />
  if (m.startsWith('stealth') || m.endsWith('-alpha')) return <img src="/models/stealth.svg" alt="" style={style} />
  if (m.includes('muse') || m.includes('meta/')) return <img src="/models/meta.svg" alt="" style={style} />
  if (m.startsWith('x-ai/') || m.includes('grok')) return <img src={dark ? '/models/grok-dark.svg' : '/models/grok.svg'} alt="" style={style} />
  if (m.includes('longcat')) return <img src="/models/longcat-color.svg" alt="" style={style} />
  if (m.replace(/[-_\s]/g, '').includes('commandcode')) return <img src={dark ? '/models/commandcode-dark.svg' : '/models/commandcode.svg'} alt="" style={style} />
  return null
}

type BadgeTheme = { light: string; dark: string }

const PROVIDER_BADGES: Record<string, { color: string; bg: BadgeTheme; text: BadgeTheme }> = {
  'tencent/': { color: '#0052D9', bg: { light: '#0052D926', dark: '#0052D980' }, text: { light: '#003a8c', dark: '#d0dff5' } },
  anthropic: { color: '#cc7c5e', bg: { light: '#cc7c5e26', dark: '#cc7c5e40' }, text: { light: '#975a3d', dark: '#e8a878' } },
  google: { color: '#528af2', bg: { light: '#528af226', dark: '#528af240' }, text: { light: '#1e40af', dark: '#a5b4fc' } },
  openai: { color: '#dcdcdc', bg: { light: '#dcdcdc80', dark: '#dcdcdc90' }, text: { light: '#475569', dark: '#0f172a' } },
  minimax: { color: '#ec6b53', bg: { light: '#ec6b5326', dark: '#ec6b5340' }, text: { light: '#b91c1c', dark: '#fca5a5' } },
  xiaomi: { color: '#dcc496', bg: { light: '#dcc49680', dark: '#dcc49690' }, text: { light: '#6b4f2a', dark: '#dcc496' } },
  openrouter: { color: '#6366f1', bg: { light: '#6366f126', dark: '#6366f140' }, text: { light: '#6366f1', dark: '#a5b4fc' } },
  poolside: { color: '#f97316', bg: { light: '#f9731626', dark: '#f9731640' }, text: { light: '#c2410c', dark: '#fdba74' } },
  deepseek: { color: '#4d7cff', bg: { light: '#4d7cff26', dark: '#4d7cff40' }, text: { light: '#1d4ed8', dark: '#93b4ff' } },
  doubao: { color: '#00CBD4', bg: { light: '#00CBD426', dark: '#00CBD440' }, text: { light: '#00a0a8', dark: '#5ee7f0' } },
  seed: { color: '#00CBD4', bg: { light: '#00CBD426', dark: '#00CBD440' }, text: { light: '#00a0a8', dark: '#5ee7f0' } },
  'z-ai': { color: '#1F63EC', bg: { light: '#1F63EC26', dark: '#1F63EC40' }, text: { light: '#1F63EC', dark: '#7daaf5' } },
  stepfun: { color: '#01A9FF', bg: { light: '#01A9FF26', dark: '#01A9FF40' }, text: { light: '#006f9f', dark: '#7dd5fc' } },
  volce: { color: '#0095FD', bg: { light: '#0095FD26', dark: '#0095FD40' }, text: { light: '#0070c0', dark: '#66c2ff' } },
  qwen: { color: '#6F69F7', bg: { light: '#6F69F726', dark: '#6F69F740' }, text: { light: '#6336E7', dark: '#a5a0fc' } },
  dots: { color: '#8FE2D6', bg: { light: '#8FE2D626', dark: '#8FE2D640' }, text: { light: '#0c4540', dark: '#d8fdfb' } },
}

function findProviderBadge(provider: string) {
  const p = provider.toLowerCase()
  for (const [key, val] of Object.entries(PROVIDER_BADGES)) {
    if (key.endsWith('/') ? p.startsWith(key) : p.includes(key)) return val
  }
  return null
}

export function getProviderBadgeBg(provider: string, theme: Theme = getTheme()): string {
  return findProviderBadge(provider)?.bg[theme] ?? (theme === 'dark' ? '#334155' : '#f1f5f9')
}

export function getProviderBadgeText(provider: string, theme: Theme = getTheme()): string {
  return findProviderBadge(provider)?.text[theme] ?? (theme === 'dark' ? '#94a3b8' : '#475569')
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

const SOURCE_BADGES: Record<string, { bg: BadgeTheme; text: BadgeTheme }> = {
  'codex': { bg: { light: '#dcdcdc80', dark: '#dcdcdc90' }, text: { light: '#475569', dark: '#0f172a' } },
  'claude-code': { bg: { light: '#cc7c5e26', dark: '#cc7c5e40' }, text: { light: '#cc7c5e', dark: '#cc7c5e' } },
  'gemini-cli': { bg: { light: '#528af226', dark: '#528af240' }, text: { light: '#528af2', dark: '#528af2' } },
  'proxy': { bg: { light: '#8b5cf626', dark: '#8b5cf640' }, text: { light: '#8b5cf6', dark: '#8b5cf6' } },
  'opencode': { bg: { light: '#10b98126', dark: '#10b98140' }, text: { light: '#10b981', dark: '#10b981' } },
  'kilo': { bg: { light: '#06b6d426', dark: '#06b6d440' }, text: { light: '#06b6d4', dark: '#06b6d4' } },
}

export function getSourceBadgeBg(name: string): string {
  const theme = getTheme()
  return SOURCE_BADGES[name]?.bg[theme] ?? (theme === 'dark' ? '#334155' : '#f1f5f9')
}

export function getSourceBadgeText(name: string): string {
  const theme = getTheme()
  return SOURCE_BADGES[name]?.text[theme] ?? (theme === 'dark' ? '#94a3b8' : '#475569')
}

export function getSourceIcon(source: string, theme: Theme = getTheme()) {
  const s = source.toLowerCase()
  const style = ICON_STYLE
  const dark = theme === 'dark'
  if (s.includes('hermes')) return <img src={dark ? '/models/hermesagent-dark.png' : '/models/hermesagent.svg'} alt="" style={style} />
  if (s.includes('codex')) return <img src="/models/codex-color.svg" alt="" style={style} />
  if (s.includes('opencode') || s.includes('open-code')) return <img src="/models/opencode.svg" alt="" style={style} />
  if (s.includes('claude')) return <img src="/models/claude-ai-icon.svg" alt="" style={style} />
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

export function sessionDisplayName(session: { client_source: string }) {
  return `${sessionAgentName(session.client_source)} session`
}

type SessionTitleInput = {
  client_source: string
  evaluation?: {
    task_title?: string | null
    task_title_zh?: string | null
  } | null
}

function firstNonEmpty(values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    const trimmed = value?.trim()
    if (trimmed) return trimmed
  }
  return null
}

export function sessionTaskTitle(session: SessionTitleInput, lang: Lang) {
  const localizedTitle = lang === 'zh'
    ? firstNonEmpty([session.evaluation?.task_title_zh, session.evaluation?.task_title])
    : firstNonEmpty([session.evaluation?.task_title, session.evaluation?.task_title_zh])

  return localizedTitle ?? sessionDisplayName(session)
}

export type SessionInsight = {
  key: string
  title: string
  session: import('./types').SessionSummary
  value: string
  detail: string
  tone?: 'warning' | 'danger' | 'success'
}

export function buildSessionInsights(sessions: import('./types').SessionSummary[]): SessionInsight[] {
  if (sessions.length === 0) return []

  const insightSessions = [...sessions]
  const mostExpensive = [...insightSessions].sort((a, b) => b.total_cost_usd - a.total_cost_usd)[0]
  const slowest = [...insightSessions].sort((a, b) => b.avg_latency_ms - a.avg_latency_ms)[0]
  const tokenBurner = [...insightSessions].sort((a, b) => b.total_tokens - a.total_tokens)[0]
  const cacheSaver = [...insightSessions].filter(session => session.cached_tokens > 0).sort((a, b) => b.cached_tokens - a.cached_tokens)[0]
  const reliabilityWatch = [...insightSessions].filter(session => session.failed_requests > 0).sort((a, b) => b.failed_requests - a.failed_requests)[0]

  const insights: SessionInsight[] = []

  if (mostExpensive && mostExpensive.total_cost_usd > 0) {
    insights.push({
      key: 'most-expensive',
      title: 'Most Expensive Session',
      session: mostExpensive,
      value: formatCost(mostExpensive.total_cost_usd, 2),
      detail: `${formatNumber(mostExpensive.request_count)} requests · ${formatCompact(mostExpensive.total_tokens)} tokens`,
    })
  }

  if (slowest && slowest.avg_latency_ms > 0) {
    insights.push({
      key: 'slowest',
      title: 'Slowest Session',
      session: slowest,
      value: formatLatency(slowest.avg_latency_ms),
      detail: `${formatNumber(slowest.request_count)} requests · ${formatDuration(slowest.duration_s)}`,
      tone: slowest.avg_latency_ms >= 5000 ? 'warning' : undefined,
    })
  }

  if (tokenBurner && tokenBurner.total_tokens > 0) {
    insights.push({
      key: 'token-burner',
      title: 'Biggest Token Burner',
      session: tokenBurner,
      value: formatCompact(tokenBurner.total_tokens),
      detail: `${formatCompact(tokenBurner.prompt_tokens)} in · ${formatCompact(tokenBurner.completion_tokens)} out`,
    })
  }

  if (cacheSaver) {
    // cache_creation_tokens is disjoint from prompt_tokens (Anthropic semantics),
    // so it must be added to the denominator for a true hit rate.
    const cacheSaverTotalInput = cacheSaver.prompt_tokens + cacheSaver.cache_creation_tokens
    insights.push({
      key: 'cache-saver',
      title: 'Best Cache Saver',
      session: cacheSaver,
      value: formatCompact(cacheSaver.cached_tokens),
      detail: `${cacheSaverTotalInput > 0 ? Math.round((cacheSaver.cached_tokens / cacheSaverTotalInput) * 100) : 0}% cache hit estimate`,
      tone: 'success',
    })
  }

  if (reliabilityWatch) {
    insights.push({
      key: 'reliability-watch',
      title: 'Reliability Watch',
      session: reliabilityWatch,
      value: `${formatNumber(reliabilityWatch.failed_requests)} failed`,
      detail: `${reliabilityWatch.request_count > 0 ? Math.round((reliabilityWatch.successful_requests / reliabilityWatch.request_count) * 100) : 0}% success rate`,
      tone: 'danger',
    })
  }

  return insights
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

// ─── Tool color map for tool-name badges ────────────────────────────────────

const TOOL_COLORS: Record<string, { bg: string; text: string }> = {
  bash:      { bg: '#2563eb', text: '#fff' },
  read:      { bg: '#059669', text: '#fff' },
  edit:      { bg: '#d97706', text: '#fff' },
  write:     { bg: '#dc2626', text: '#fff' },
  grep:      { bg: '#7c3aed', text: '#fff' },
  glob:      { bg: '#0891b2', text: '#fff' },
  task:      { bg: '#4f46e5', text: '#fff' },
  webfetch:  { bg: '#be185d', text: '#fff' },
  websearch: { bg: '#9333ea', text: '#fff' },
  skill:     { bg: '#0d9488', text: '#fff' },
  list:      { bg: '#0891b2', text: '#fff' },
  question:  { bg: '#8b5cf6', text: '#fff' },
  todowrite: { bg: '#f59e0b', text: '#fff' },
  lsp:       { bg: '#6366f1', text: '#fff' },
  exec:      { bg: '#ea580c', text: '#fff' },
  exec_command: { bg: '#bfdbfe', text: '#334155' },
  mcp_tool:  { bg: '#e11d48', text: '#fff' },
}
const TOOL_DEFAULT_COLOR = { bg: '#64748b', text: '#fff' }
const TOOL_COLORS_CI = new Map(Object.entries(TOOL_COLORS).map(([k, v]) => [k.toLowerCase(), v]))

export function getToolColor(toolName: string) {
  return TOOL_COLORS_CI.get(toolName.toLowerCase()) || TOOL_DEFAULT_COLOR
}

const TOOL_BADGE_STYLE: React.CSSProperties = {
  padding: '2px 6px',
  borderRadius: '4px',
  fontSize: '11px',
  fontWeight: 600,
  whiteSpace: 'nowrap',
  display: 'inline-block',
}

export function ToolBadge({ name, count, style }: { name: string; count?: number; style?: React.CSSProperties }) {
  const c = getToolColor(name)
  return (
    <span style={{ ...TOOL_BADGE_STYLE, backgroundColor: c.bg, color: c.text, ...style }}>
      {name}{count != null ? `×${count}` : ''}
    </span>
  )
}
