import { t } from './i18n'
import type { EffortMix } from './types'
import { formatNumber } from './utils'

// Reasoning effort levels the backend stores, least to most effort. `default`
// (the agent ran without a level) is unrated, so it gets no pips and sorts last.
const EFFORT_ORDER = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra', 'default']
const EFFORT_PIPS: Record<string, number> = { none: 0, minimal: 1, low: 1, medium: 2, high: 3, xhigh: 4, max: 5, ultra: 5 }
// Colour per level (`.effort-tone-*` in App.css): none and minimal, and max and
// ultra, are near-synonyms and share one; default and unknown levels stay muted.
const EFFORT_TONE: Record<string, string> = { none: 'none', minimal: 'none', low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh', max: 'max', ultra: 'max' }

const effortTone = (effort: string) => `effort-tone-${EFFORT_TONE[effort] ?? 'default'}`

/** Level pips plus the level name; the pips are decoration, the text is the label. */
export function EffortLevel({ effort }: { effort: string }) {
  const pips = EFFORT_PIPS[effort]
  return <span className={`effort-level ${effortTone(effort)}`}>{pips !== undefined && <span className="effort-pips" aria-hidden="true">{[1, 2, 3, 4, 5].map(i => <i key={i} className={i <= pips ? 'on' : undefined} />)}</span>}<span className="effort-name">{effort}</span></span>
}

/** Reported mix rows in level order, each with its share of the reported requests; requests without effort are left out. */
function effortMixRows(mix: EffortMix) {
  const reported = mix.filter((row): row is { effort: string; requests: number } => row.effort !== null)
  const total = reported.reduce((sum, row) => sum + row.requests, 0)
  const rank = (effort: string) => EFFORT_ORDER.includes(effort) ? EFFORT_ORDER.indexOf(effort) : EFFORT_ORDER.length
  const share = (requests: number) => {
    const pct = total > 0 ? Math.round(requests * 100 / total) : 0
    return pct === 0 && requests > 0 ? '<1%' : `${pct}%`
  }
  const rows = reported.sort((a, b) => rank(a.effort) - rank(b.effort)).map(row => ({ ...row, share: share(row.requests) }))
  return { total, rows }
}

/** Effort mix of the reported requests, or nothing when no request reported effort. */
export function EffortMixSection({ mix }: { mix: EffortMix }) {
  const { total, rows } = effortMixRows(mix)
  if (!rows.length) return null
  const all = mix.reduce((sum, row) => sum + row.requests, 0)
  const count = all > total ? t('{n} of {total} requests').replace('{n}', formatNumber(total)).replace('{total}', formatNumber(all))
    : total === 1 ? t('1 request') : t('{n} requests').replace('{n}', formatNumber(total))
  return <section className="project-breakdown effort-mix">
    <div className="effort-mix-head"><h3>{t('Effort')}</h3><span>{count}</span></div>
    <div className="effort-mix-bar" role="img" aria-label={rows.map(row => `${row.effort} ${row.share}`).join(', ')}>{rows.map(row => <i key={row.effort} className={effortTone(row.effort)} style={{ flex: row.requests }} />)}</div>
    <div className="effort-mix-legend">{rows.map(row => <span key={row.effort} className={effortTone(row.effort)}><i /><b>{row.effort}</b><em>{row.share} · {formatNumber(row.requests)}</em></span>)}</div>
  </section>
}
