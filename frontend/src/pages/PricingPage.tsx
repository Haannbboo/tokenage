import { Fragment, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import { formatNumber, getModelIcon } from '../utils'
import { usePricingData, pricingSourceOrder, type Model, type PriceField } from '../hooks/usePricingData'
import type { PricingEntry } from '../types'

const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const SOURCE_STYLES: Record<string, { bg: string; color: string; label: string }> = {
  user: { bg: 'rgba(22, 163, 74, 0.15)', color: '#16a34a', label: 'Custom' },
  yaml: { bg: 'var(--icon-yellow-bg)', color: '#b8860b', label: 'Server' },
  new: { bg: 'var(--surface-hover)', color: 'var(--text-primary)', label: 'New' },
  litellm: { bg: 'rgba(59, 130, 246, 0.15)', color: '#3b82f6', label: 'LiteLLM' },
  openrouter: { bg: 'rgba(147, 51, 234, 0.15)', color: '#9333ea', label: 'OpenRouter' },
}

function sourceStyle(source: string) {
  return SOURCE_STYLES[source] ?? { bg: 'var(--surface-hover)', color: 'var(--text-muted)', label: source }
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

const PAGE_SIZE = 200

export function PricingPage() {
  const { configStatus } = useApp()
  const {
    pricingSearch,
    setPricingSearch,
    filteredPricingModels,
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
  } = usePricingData()

  const [sourceFilter, setSourceFilter] = useState<'all' | 'user' | 'yaml' | 'litellm' | 'openrouter'>('user')
  const [newModelName, setNewModelName] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)

  useEffect(() => {
    setVisibleCount(PAGE_SIZE)
  }, [sourceFilter, pricingSearch])

  // Unsaved models added by name stay on top whatever the filter.
  const models = useMemo(
    () => [...newModels, ...(sourceFilter === 'all'
      ? filteredPricingModels
      : filteredPricingModels.filter((m) => m.source === sourceFilter))],
    [newModels, filteredPricingModels, sourceFilter],
  )

  const visibleModels = models.slice(0, visibleCount)

  const scrollRef = useRef<HTMLDivElement | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel || visibleCount >= models.length) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) setVisibleCount((c) => Math.min(c + PAGE_SIZE, models.length))
      },
      { root: scrollRef.current, rootMargin: '200px' },
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [visibleCount, models.length])

  const counts = useMemo(() => {
    const bySource: Record<string, number> = { user: 0, yaml: 0, litellm: 0, openrouter: 0 }
    for (const m of filteredPricingModels) bySource[m.source] = (bySource[m.source] ?? 0) + 1
    return bySource
  }, [filteredPricingModels])

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
      : model.source === 'user' && price !== undefined && price !== null ? String(price) : ''
    const active = value !== ''
    return {
      type: 'number' as const,
      step: '0.001',
      min: '0',
      disabled: saving,
      value,
      placeholder: model.source !== 'new' && price !== undefined && price !== null ? String(price) : '—',
      onChange: (e: ChangeEvent<HTMLInputElement>) => handleCostChange(model, field, e.target.value),
      style: {
        width: '100%',
        padding: '6px 8px',
        borderRadius: '4px',
        border: '1px solid transparent',
        borderBottom: '1px solid var(--border-color)',
        background: active ? 'var(--input-bg)' : 'transparent',
        fontSize: '13px',
        color: active ? 'var(--text-primary)' : 'var(--text-muted)',
        outline: 'none',
        textAlign: 'left' as const,
      },
    }
  }

  const statCard = (label: string, value: string | number, accent?: string, opts?: { key?: string; active?: boolean; onClick?: () => void; rank?: number }) => (
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
        borderRadius: '10px',
        background: 'var(--surface-hover)',
        border: `1px solid ${opts?.active ? (accent ?? 'var(--text-primary)') : 'var(--border-color)'}`,
        cursor: opts?.onClick ? 'pointer' : 'default',
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.4px', color: 'var(--text-muted)' }}>
        {opts?.rank !== undefined && (
          <span
            title={t('Priority')}
            style={{
              fontSize: '10px',
              fontWeight: 700,
              padding: '1px 6px',
              borderRadius: '8px',
              background: accent ?? 'var(--text-muted)',
              color: '#fff',
            }}
          >
            #{opts.rank}
          </span>
        )}
        <span>{label}</span>
      </div>
      <div style={{ marginTop: '4px', fontSize: '20px', fontWeight: 700, color: accent ?? 'var(--text-primary)' }}>{value}</div>
    </div>
  )

  const toggleSourceFilter = (source: 'user' | 'yaml' | 'litellm' | 'openrouter') => {
    setSourceFilter((prev) => (prev === source ? 'all' : source))
  }

  const sourceOrder = useMemo(
    () => pricingSourceOrder(filteredPricingModels.map((m) => m.source)),
    [filteredPricingModels],
  )

  return (
    <div className="settings-page" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="panel">
        <div className="panel-tabs" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <div className="tab active"><span>💎</span> {t('Model Pricing')}</div>
          <div style={{ paddingRight: '16px', display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: 'var(--text-muted)' }}>
              <input
                type="checkbox"
                checked={repricePast}
                disabled={saving}
                onChange={(e) => setRepricePast(e.target.checked)}
              />
              {t('Reprice past usage')}
            </label>
            <button
              type="button"
              className="nav-item"
              disabled={!hasPricingEdits || saving}
              onClick={handleSavePricing}
              style={{
                fontSize: '12px',
                fontWeight: 600,
                padding: '6px 14px',
                borderRadius: '6px',
                opacity: hasPricingEdits && !saving ? 1 : 0.5,
                cursor: hasPricingEdits && !saving ? 'pointer' : 'not-allowed',
              }}
            >
              {t('Save Pricing')}
            </button>
          </div>
        </div>
        <div className="panel-body" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
          <div style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {t('Precedence: your Custom prices win, then Server config; otherwise the configured price sources are checked in order (earlier wins), then a containing-name fallback.')}
          </div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
            {sourceOrder.map((key, index) => {
              if (key !== 'user' && key !== 'yaml' && key !== 'litellm' && key !== 'openrouter') return null
              const style = sourceStyle(key)
              const filter = key as 'user' | 'yaml' | 'litellm' | 'openrouter'
              return statCard(t(style.label), counts[key] ?? 0, style.color, { key, active: sourceFilter === filter, onClick: () => toggleSourceFilter(filter), rank: index + 1 })
            })}
          </div>
          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
            <input
              type="text"
              placeholder={t('Search models...')}
              value={pricingSearch}
              onChange={(e) => setPricingSearch(e.target.value)}
              style={{
                flex: '1 1 240px',
                padding: '6px 12px',
                borderRadius: '6px',
                border: '1px solid var(--border-color)',
                fontSize: '13px',
                background: 'var(--input-bg)',
                color: 'var(--text-primary)',
                outline: 'none',
              }}
            />
            <form
              onSubmit={(e) => {
                e.preventDefault()
                if (!addModel(newModelName)) setSourceFilter('all')
                setNewModelName('')
              }}
              style={{ display: 'flex', gap: '6px', flex: '1 1 240px' }}
            >
              <input
                type="text"
                placeholder={t('Model name to price...')}
                value={newModelName}
                onChange={(e) => setNewModelName(e.target.value)}
                style={{
                  flex: 1,
                  padding: '6px 12px',
                  borderRadius: '6px',
                  border: '1px solid var(--border-color)',
                  fontSize: '13px',
                  background: 'var(--input-bg)',
                  color: 'var(--text-primary)',
                  outline: 'none',
                }}
              />
              <button
                type="submit"
                className="nav-item"
                disabled={!newModelName.trim()}
                style={{ fontSize: '12px', fontWeight: 600, padding: '6px 14px', borderRadius: '6px' }}
              >
                {t('Add model')}
              </button>
            </form>
          </div>
        </div>
      </div>

      <div className="panel">
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
                <th style={{ width: '80px' }} />
              </tr>
            </thead>
            <tbody>
              {visibleModels.length > 0 ? visibleModels.map((model) => {
                const style = sourceStyle(model.source)
                const hasTiers = (model.tiers?.length ?? 0) > 0
                const hasWindows = (model.time_rates?.length ?? 0) > 0
                const showDetails = expanded === model.name
                return (
                  <Fragment key={model.name}>
                    <tr>
                      <td style={{ fontWeight: 600 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          {getModelIcon(model.name)}
                          <span>{model.name}</span>
                        </div>
                      </td>
                      <td>
                        <span
                          style={{
                            fontSize: '10px',
                            fontWeight: 600,
                            padding: '2px 6px',
                            borderRadius: '4px',
                            background: style.bg,
                            color: style.color,
                          }}
                        >
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
                            className="nav-item"
                            onClick={() => setExpanded(showDetails ? null : model.name)}
                            style={{ fontSize: '12px', padding: '4px 8px' }}
                          >
                            {showDetails ? '−' : '+'}
                          </button>
                        )}
                      </td>
                      <td>
                        {drafts[model.name] ? (
                          <button
                            type="button"
                            className="nav-item"
                            disabled={saving}
                            onClick={() => dropDraft(model.name)}
                            style={{ fontSize: '12px', padding: '4px 8px' }}
                          >
                            {t('Discard')}
                          </button>
                        ) : model.source === 'user' && (
                          <button
                            type="button"
                            className="nav-item"
                            disabled={saving}
                            title={t('Remove your custom price for this model')}
                            onClick={() => handleResetPricing(model.name)}
                            style={{ fontSize: '12px', padding: '4px 8px' }}
                          >
                            {t('Reset')}
                          </button>
                        )}
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
                      : sourceFilter !== 'all'
                        ? t('No models for this source yet.')
                        : t('No pricing data available.')}
                  </td>
                </tr>
              )}
              {visibleCount < models.length && (
                <tr>
                  <td colSpan={8} style={{ padding: '0', border: 'none' }}>
                    <div ref={sentinelRef} style={{ height: '1px' }} />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {models.length > 0 && (
          <div style={{ padding: '8px 16px', fontSize: '11px', color: 'var(--text-muted)', borderTop: '1px solid var(--border-color)' }}>
            {t('Showing')} {visibleModels.length} / {models.length} {t('models')}
          </div>
        )}
      </div>
    </div>
  )
}
