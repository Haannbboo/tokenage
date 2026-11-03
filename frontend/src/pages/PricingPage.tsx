import { Fragment, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { ReadState } from '../components/ReadState'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import { formatNumber, getModelIcon } from '../utils'
import { usePricingData, pricingSourceOrder, DEFAULT_SOURCES, type Model, type PriceField } from '../hooks/usePricingData'
import type { PricingEntry } from '../types'

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// Custom prices get the accent tint; fetched sources stay neutral.
const SOURCE_STYLES: Record<string, { badge: string; label: string }> = {
  user: { badge: 'badge-success', label: 'Custom' },
  // Server config prices merge into Custom: users see one set of their prices.
  server: { badge: 'badge-success', label: 'Custom' },
  new: { badge: 'badge-neutral', label: 'New' },
  litellm: { badge: 'badge-neutral', label: 'LiteLLM' },
  openrouter: { badge: 'badge-neutral', label: 'OpenRouter' },
}

function sourceStyle(source: string) {
  return SOURCE_STYLES[source] ?? { badge: 'badge-neutral', label: source }
}

function formatMinutes(minute: number): string {
  const m = minute % (24 * 60)
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

function formatWindow(start: number, end: number): string {
  const endLabel = end >= 24 * 60 || (end === 0 && start !== 0) ? '24:00' : formatMinutes(end)
  return `${formatMinutes(start)}–${endLabel}`
}

function formatDays(days: number[] | null): string {
  if (!days) return t('Every day')
  return days.map((day) => DAY_LABELS[day] ?? String(day)).join(', ')
}

export function PricingPage() {
  const { configStatus, error } = useApp()
  const {
    sources,
    setSources,
    pricingSearch,
    setPricingSearch,
    models: pricedModels,
    counts,
    pageState,
    refreshing,
    refetchPricing,
    total,
    hasMore,
    loadMore,
    newModels,
    drafts,
    hasPricingEdits,
    repricePast,
    setRepricePast,
    addModel,
    dropDraft,
    handleCostChange,
    handleSavePricing,
    handleResetPricing,
    repricing,
    handleRepriceModel,
  } = usePricingData()

  const [expanded, setExpanded] = useState<string | null>(null)

  // Unsaved models added by name stay on top whatever the view.
  const visibleModels = useMemo(() => [...newModels, ...pricedModels], [newModels, pricedModels])

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  // The next page loads when the end of the list scrolls into view.
  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel || !hasMore) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) loadMore()
      },
      { root: scrollRef.current, rootMargin: '200px' },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasMore, loadMore])

  const formatPrice = (v: number | null | undefined) => (typeof v === 'number' ? v.toFixed(3) : '—')

  const modelPrice = (model: Model, field: PriceField) => {
    if (field === 'cacheRead') return model.cache_read
    if (field === 'cacheWrite') return model.cache_write
    return model[field as keyof PricingEntry] as number | null | undefined
  }

  const saving = configStatus === 'saving'

  const inputProps = (model: Model, field: PriceField) => {
    const price = modelPrice(model, field)
    const draft = drafts[model.name]
    // Only drafts and the caller's own overrides show active values; other
    // sources show their price as a placeholder.
    const value = draft
      ? draft[field]
      : (model.source === 'user' || model.source === 'server') && price !== undefined && price !== null ? String(price) : ''
    const active = value !== ''
    return {
      type: 'number' as const,
      step: '0.001',
      min: '0',
      disabled: saving,
      value,
      placeholder: model.source !== 'new' && price !== undefined && price !== null ? String(price) : '—',
      onChange: (e: ChangeEvent<HTMLInputElement>) => handleCostChange(model, field, e.target.value),
      className: 'input-plain',
      style: { width: '100%', height: '28px', color: active ? 'var(--text-primary)' : 'var(--text-muted)' },
    }
  }

  const statCard = (label: string, value: string | number, opts?: { key?: string; active?: boolean; onClick?: () => void; rank?: number }) => (
    <div
      key={opts?.key}
      onClick={opts?.onClick}
      role={opts?.onClick ? 'button' : undefined}
      tabIndex={opts?.onClick ? 0 : undefined}
      onKeyDown={opts?.onClick ? (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          opts.onClick?.()
        }
      } : undefined}
      style={{
        flex: '1 1 140px',
        minWidth: '140px',
        padding: '12px 14px',
        borderRadius: 'var(--r-md)',
        background: opts?.active ? 'var(--surface-sunken)' : 'var(--card-bg)',
        border: `1px solid ${opts?.active ? 'var(--text-muted)' : 'var(--border-color)'}`,
        cursor: opts?.onClick ? 'pointer' : 'default',
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
        {opts?.rank !== undefined && (
          <span title={t('Priority')} className="badge badge-neutral">
            #{opts.rank}
          </span>
        )}
        <span>{label}</span>
      </div>
      <div style={{ marginTop: '4px', fontSize: '20px', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  )

  // Custom is the user's own prices merged with the server's, one card. A
  // fetched-source card shows only that source; clicking it again returns
  // to Custom.
  const isOnly = (source: string) => sources.length === 1 && sources[0] === source
  const toggleSource = (source: string) =>
    setSources(source === 'user' || isOnly(source) ? DEFAULT_SOURCES : [source])
  // Counts from another view would read as current: show a dash until this one loads.
  const cardCount = (key: string) => pageState !== 'ready' ? '—' : key === 'user' ? (counts.user ?? 0) + (counts.server ?? 0) : counts[key] ?? 0

  // Searching the Custom view for a name nothing lists offers to price it.
  const searchTerm = pricingSearch.trim().toLowerCase()
  const canAdd = sources.includes('user') && searchTerm !== ''
    && !drafts[searchTerm] && !visibleModels.some((m) => m.name.toLowerCase() === searchTerm)

  const sourceOrder = useMemo(
    () => pricingSourceOrder(Object.keys(counts)).filter((key) => ['user', 'litellm', 'openrouter'].includes(key)),
    [counts],
  )

  return (
    <div className="settings-page" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="panel">
        <div className="panel-tabs" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <div className="tab active">{t('Model Pricing')}</div>
          <div style={{ paddingRight: '16px', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            {error && <span role="alert" style={{ fontSize: '12px', color: 'var(--color-red)' }}>{error}</span>}
            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
              <input
                type="checkbox"
                checked={repricePast}
                disabled={saving}
                onChange={(e) => setRepricePast(e.target.checked)}
              />
              {t('Reprice unpriced usage')}
            </label>
            <button
              type="button"
              className="btn-primary"
              disabled={!hasPricingEdits || saving}
              onClick={handleSavePricing}
            >
              {t('Save Pricing')}
            </button>
          </div>
        </div>
        <div className="panel-body" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {t('Precedence: your Custom prices win; otherwise the configured price sources are checked in order (earlier wins), then a containing-name fallback.')}
          </div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            {sourceOrder.map((key, index) => {
              const style = sourceStyle(key)
              return statCard(t(style.label), cardCount(key), { key, active: sources.includes(key), onClick: () => toggleSource(key), rank: index + 1 })
            })}
          </div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              type="text"
              placeholder={t('Search models...')}
              value={pricingSearch}
              onChange={(e) => setPricingSearch(e.target.value)}
              className="input-plain"
              style={{ flex: '1 1 240px' }}
            />
            {canAdd && (
              <button
                type="button"
                className="btn-ghost"
                onClick={() => void addModel(searchTerm)}
              >
                {t('Add')} “{searchTerm}”
              </button>
            )}
          </div>
        </div>
      </div>

      {/* A reload of this view (after a save) keeps the table, with the dashboard's refresh sheen. */}
      <div className={`panel refresh-sheen${refreshing ? ' is-refreshing' : ''}`}>
        <div ref={scrollRef} className="panel-body" style={{ padding: '0', maxHeight: '640px', overflowY: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '220px' }}>{t('Model')}</th>
                <th style={{ width: '90px' }}>{t('Source')}</th>
                <th>{t('Input (per 1M)')}</th>
                <th>{t('Output (per 1M)')}</th>
                <th>{t('Cache Read (per 1M)')}</th>
                <th>{t('Cache Write (per 1M)')}</th>
                <th style={{ width: '80px' }}>{t('Details')}</th>
                <th style={{ width: '150px' }} />
              </tr>
            </thead>
            <tbody>
              {pageState !== 'ready' ? (
                <tr>
                  <td colSpan={8} style={{ padding: 0 }}>
                    <ReadState error={pageState === 'failed' ? t('Failed to load pricing') : null} onRetry={() => void refetchPricing()} />
                  </td>
                </tr>
              ) : visibleModels.length > 0 ? visibleModels.map((model) => {
                const style = sourceStyle(model.source)
                const hasTiers = (model.tiers?.length ?? 0) > 0
                const hasWindows = (model.time_rates?.length ?? 0) > 0
                const showDetails = expanded === model.name
                return (
                  <Fragment key={model.name}>
                    <tr>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          {getModelIcon(model.name)}
                          <span className="mono">{model.name}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${style.badge}`}>
                          {t(style.label)}
                        </span>
                      </td>
                      <td>
                        <input {...inputProps(model, 'input')} />
                      </td>
                      <td>
                        <input {...inputProps(model, 'output')} />
                      </td>
                      <td>
                        <input {...inputProps(model, 'cacheRead')} />
                      </td>
                      <td>
                        <input {...inputProps(model, 'cacheWrite')} />
                      </td>
                      <td>
                        {(hasTiers || hasWindows) && (
                          <button
                            type="button"
                            className="btn-ghost"
                            onClick={() => setExpanded(showDetails ? null : model.name)}
                            style={{ padding: '3px 8px' }}
                          >
                            {showDetails ? '−' : '+'}
                          </button>
                        )}
                      </td>
                      <td>
                        <div style={{ display: 'flex', gap: '4px', justifyContent: 'flex-end' }}>
                        {model.source !== 'new' && !drafts[model.name] && (
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={saving || repricing === model.name}
                            title={t('Reprice your unpriced usage of this model at its current price')}
                            onClick={() => void handleRepriceModel(model.name)}
                            style={{ padding: '3px 8px' }}
                          >
                            {t('Reprice')}
                          </button>
                        )}
                        {drafts[model.name] ? (
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={saving}
                            onClick={() => dropDraft(model.name)}
                            style={{ padding: '3px 8px' }}
                          >
                            {t('Discard')}
                          </button>
                        ) : model.source === 'user' && model.override === model.name && (
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={saving}
                            title={t('Remove your custom price for this model')}
                            onClick={() => handleResetPricing(model.name)}
                            style={{ padding: '3px 8px' }}
                          >
                            {t('Reset')}
                          </button>
                        )}
                        </div>
                      </td>
                    </tr>
                    {showDetails && (hasTiers || hasWindows) && (
                      <tr>
                        <td colSpan={8} style={{ background: 'var(--surface-hover)', padding: '12px 16px' }}>
                          {hasTiers && (
                            <div style={{ marginBottom: hasWindows ? '12px' : 0 }}>
                              <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: '6px' }}>{t('Token tiers')}</div>
                              <table className="table">
                                <thead>
                                  <tr>
                                    <th>{t('Range (tokens)')}</th>
                                    <th>{t('Input')}</th>
                                    <th>{t('Output')}</th>
                                    <th>{t('Cache Read')}</th>
                                    <th>{t('Cache Write')}</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {model.tiers?.map((tier, index) => (
                                    <tr key={index}>
                                      <td>{tier.max_tokens === null ? `${formatNumber(tier.min_tokens)}+` : `${formatNumber(tier.min_tokens)}–${formatNumber(tier.max_tokens)}`}</td>
                                      <td>{formatPrice(tier.input)}</td>
                                      <td>{formatPrice(tier.output)}</td>
                                      <td>{formatPrice(tier.cache_read)}</td>
                                      <td>{formatPrice(tier.cache_write)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                          {hasWindows && (
                            <div>
                              <div style={{ fontSize: '12px', fontWeight: 700, marginBottom: '6px' }}>{t('Time-of-day rates')}</div>
                              <table className="table">
                                <thead>
                                  <tr>
                                    <th>{t('Window (UTC)')}</th>
                                    <th>{t('Days')}</th>
                                    <th>{t('Input')}</th>
                                    <th>{t('Output')}</th>
                                    <th>{t('Cache Read')}</th>
                                    <th>{t('Cache Write')}</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {model.time_rates?.map((window, index) => (
                                    <tr key={index}>
                                      <td>{formatWindow(window.start_minute, window.end_minute)}</td>
                                      <td>{formatDays(window.days)}</td>
                                      <td>{formatPrice(window.input)}</td>
                                      <td>{formatPrice(window.output)}</td>
                                      <td>{formatPrice(window.cache_read)}</td>
                                      <td>{formatPrice(window.cache_write)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              }) : (
                <tr>
                  <td colSpan={8} style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
                    {pricingSearch
                      ? t('No models match your search.')
                      : t('No models for this source yet.')}
                  </td>
                </tr>
              )}
              {hasMore && (
                <tr>
                  <td colSpan={8} style={{ padding: '0', border: 'none' }}>
                    <div ref={sentinelRef} style={{ height: '1px' }} />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {total > 0 && (
          <div style={{ padding: '8px 16px', fontSize: '11px', color: 'var(--text-muted)', borderTop: '1px solid var(--border-color)' }}>
            {t('Showing')} {pricedModels.length} / {total} {t('models')}
          </div>
        )}
      </div>
    </div>
  )
}
