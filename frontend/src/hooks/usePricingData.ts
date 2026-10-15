import { useState, useCallback, useMemo, useEffect, useRef } from 'react'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import type { PricingEntry, PricingSource } from '../types.ts'

export type PriceField = 'input' | 'output' | 'cacheRead' | 'cacheWrite'
type PriceDraft = Record<PriceField, string>
/** isNew marks a model added by name that no source lists. */
type Draft = PriceDraft & { isNew?: boolean }
/** A listed row; source 'new' marks an unsaved model added by name. */
export type Model = { name: string } & Omit<PricingEntry, 'source'> & { source: PricingSource | 'new' }

/** The first page shows only what the caller and the server price; the large
 *  fetched sources load when their card is picked. */
export const DEFAULT_SOURCES = ['user', 'server']
const PAGE_SIZE = 200

/** The model's current effective flat rate as editable strings. */
function draftFrom(model?: Model): PriceDraft {
  if (!model || model.source === 'new') return { input: '', output: '', cacheRead: '', cacheWrite: '' }
  return {
    input: String(model.input),
    output: String(model.output),
    cacheRead: model.cache_read == null ? '' : String(model.cache_read),
    cacheWrite: model.cache_write == null ? '' : String(model.cache_write),
  }
}

/** Parse a draft into the override body; returns a field name when invalid. */
export function overrideBody(draft: PriceDraft): Record<string, any> | string {
  const body: Record<string, any> = {}
  const keys: Record<PriceField, string> = { input: 'input', output: 'output', cacheRead: 'cache_read', cacheWrite: 'cache_write' }
  for (const field of ['input', 'output', 'cacheRead', 'cacheWrite'] as PriceField[]) {
    const raw = draft[field].trim()
    if (raw === '') {
      if (field === 'input' || field === 'output') return field
      body[keys[field]] = null
      continue
    }
    const value = Number(raw)
    if (!Number.isFinite(value) || value < 0) return field
    body[keys[field]] = value
  }
  return body
}

/** Pricing source keys in resolution-priority order: the caller's own
 *  overrides, then server config, then the sources seen in the pricing data. */
export function pricingSourceOrder(seenSources?: string[]): string[] {
  const seen = new Set(seenSources ?? [])
  const order = ['user', 'server']
  for (const name of ['openrouter', 'litellm']) {
    if (seen.size === 0 || seen.has(name)) order.push(name)
  }
  for (const name of [...seen].sort()) {
    if (!order.includes(name)) order.push(name)
  }
  return order
}

function pricingUrl(sources: string[], q: string, offset: number, limit = PAGE_SIZE) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (sources.length) params.set('source', sources.join(','))
  if (q) params.set('q', q)
  return `/pricing?${params}`
}

export function usePricingData() {
  const { setConfigStatus, setError, showToast } = useApp()

  const [sources, setSources] = useState<string[]>(DEFAULT_SOURCES)
  const [pricingSearch, setPricingSearch] = useState('')
  const [query, setQuery] = useState('')
  const [models, setModels] = useState<Model[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(false)
  // A failed page stops automatic loading until the view changes, so a
  // visible sentinel can't re-request a broken page in a loop.
  const [listFailed, setListFailed] = useState(false)
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [repricePast, setRepricePast] = useState(true)
  const [repricing, setRepricing] = useState<string | null>(null)
  // Monotonic id: only the latest view's response may update state, so a
  // slow earlier page can't land in a newer filter or search.
  const seqRef = useRef(0)

  useEffect(() => {
    const timer = setTimeout(() => setQuery(pricingSearch.trim()), 250)
    return () => clearTimeout(timer)
  }, [pricingSearch])

  /** Fetch one page of the current view; offset 0 replaces the list. */
  const fetchPage = useCallback(async (offset: number) => {
    const seq = offset === 0 ? ++seqRef.current : seqRef.current
    if (offset === 0) setListFailed(false)
    setLoading(true)
    try {
      const response = await fetch(pricingUrl(sources, query, offset))
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      // Checked after the body: a slow older body must not land in a newer view.
      if (seq !== seqRef.current) return
      setModels((prev) => (offset === 0 ? data.models : [...prev, ...data.models]))
      setTotal(data.total)
      setCounts(data.counts)
    } catch (err) {
      if (seq !== seqRef.current) return
      console.error('Failed to load pricing:', err)
      setError(t('Failed to load pricing'))
      setListFailed(true)
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [sources, query, setError])

  useEffect(() => {
    void fetchPage(0)
  }, [fetchPage])

  const hasMore = !listFailed && models.length < total
  const loadMore = useCallback(() => {
    if (!loading && hasMore) void fetchPage(models.length)
  }, [fetchPage, hasMore, loading, models.length])

  const refetchPricing = useCallback(() => fetchPage(0), [fetchPage])

  /** Draft-only rows: models added by name that no source prices yet. A
   *  saved model whose reprice failed keeps its draft but is listed already. */
  const newModels = useMemo(
    () => Object.entries(drafts)
      .filter(([name, draft]) => draft.isNew && !models.some((m) => m.name === name))
      .map(([name]): Model => ({ name, input: 0, output: 0, cache_read: 0, cache_write: null, source: 'new' })),
    [drafts, models],
  )

  const handleCostChange = useCallback((model: Model, field: PriceField, val: string) => {
    setDrafts((prev) => {
      // The first edit prefills the model's effective prices so the user
      // edits the full flat rate, not one field of it.
      const draft = prev[model.name] ?? draftFrom(model)
      return { ...prev, [model.name]: { ...draft, [field]: val } }
    })
  }, [])

  const dropDraft = useCallback((model: string) => {
    setDrafts((prev) => {
      const next = { ...prev }
      delete next[model]
      return next
    })
  }, [])

  /** Start a draft for a model by name. When a source already lists it, the
   *  view switches to that model instead. */
  const addModel = useCallback(async (name: string) => {
    // The server stores override names lowercased, so match and key the same way.
    const model = name.trim().toLowerCase()
    if (!model || drafts[model]) return
    try {
      const response = await fetch(pricingUrl([], model, 0, 1000))
      if (response.ok) {
        const data = await response.json()
        const existing = (data.models as Model[]).find((m) => m.name.toLowerCase() === model)
        if (existing) {
          // Server prices live in the merged Custom view, never on their own.
          setSources(DEFAULT_SOURCES.includes(existing.source) ? DEFAULT_SOURCES : [existing.source])
          setPricingSearch(existing.name)
          return
        }
      }
    } catch {
      /* fall through: price it as a new model */
    }
    setDrafts((prev) => (prev[model] ? prev : { ...prev, [model]: { ...draftFrom(), isNew: true } }))
    setPricingSearch('')
  }, [drafts])

  const handleSavePricing = useCallback(async () => {
    const entries = Object.entries(drafts)
    if (entries.length === 0) return
    setError(null)
    const bodies: Array<[string, Record<string, any>]> = []
    for (const [model, draft] of entries) {
      const body = overrideBody(draft)
      if (typeof body === 'string') {
        setError(`${t('Invalid price')}: ${model} (${body})`)
        setConfigStatus('error')
        return
      }
      bodies.push([model, body])
    }
    setConfigStatus('saving')
    let repriced = 0
    try {
      for (const [model, body] of bodies) {
        const path = encodeURIComponent(model)
        const response = await fetch(`/pricing/overrides/${path}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
        if (!response.ok) {
          const error = await response.json().catch(() => ({}))
          setError(`${model}: ${typeof error.detail === 'string' ? error.detail : t('Failed to save pricing')}`)
          setConfigStatus('error')
          return
        }
        if (repricePast) {
          const repriceResp = await fetch(`/usage/reprice?model=${path}`, { method: 'POST' })
          if (!repriceResp.ok) {
            setError(`${model}: ${t('Failed to reprice unpriced usage')}`)
            setConfigStatus('error')
            return
          }
          repriced += (await repriceResp.json()).repriced ?? 0
        }
        // Only now: a failed save or reprice keeps the edit for a retry.
        dropDraft(model)
      }
      setConfigStatus('saved')
      // Saved rows are Custom now; show them rather than a view that hides them.
      setSources((prev) => (prev.includes('user') ? prev : DEFAULT_SOURCES))
      showToast(repricePast
        ? `${t('Pricing saved; repriced usage rows:')} ${repriced}`
        : t('Pricing saved'))
      setTimeout(() => setConfigStatus('idle'), 3000)
    } catch {
      setError(t('Connection error while saving pricing'))
      setConfigStatus('error')
    } finally {
      // Saved models resolve to their override now, even after a failure.
      await refetchPricing()
    }
  }, [
    drafts,
    dropDraft,
    repricePast,
    refetchPricing,
    setConfigStatus,
    setError,
    showToast,
  ])

  const handleResetPricing = useCallback(async (model: string) => {
    setError(null)
    try {
      const response = await fetch(`/pricing/overrides/${encodeURIComponent(model)}`, { method: 'DELETE' })
      if (!response.ok && response.status !== 404) {
        const error = await response.json().catch(() => ({}))
        setError(`${model}: ${typeof error.detail === 'string' ? error.detail : t('Failed to reset pricing')}`)
        return
      }
      dropDraft(model)
      showToast(t('Custom pricing removed'))
    } catch {
      setError(t('Connection error while resetting pricing'))
      return
    }
    await refetchPricing()
  }, [dropDraft, refetchPricing, setError, showToast])

  /** Price the caller's unpriced usage of one model at its current price. */
  const handleRepriceModel = useCallback(async (model: string) => {
    setError(null)
    setRepricing(model)
    try {
      const response = await fetch(`/usage/reprice?model=${encodeURIComponent(model)}`, { method: 'POST' })
      if (!response.ok) {
        setError(`${model}: ${t('Failed to reprice unpriced usage')}`)
        return
      }
      showToast(`${model}: ${t('repriced usage rows:')} ${(await response.json()).repriced ?? 0}`)
    } catch {
      setError(t('Connection error while repricing'))
    } finally {
      setRepricing(null)
    }
  }, [setError, showToast])

  return {
    sources,
    setSources,
    pricingSearch,
    setPricingSearch,
    models,
    counts,
    total,
    hasMore,
    loadMore,
    newModels,
    drafts,
    hasPricingEdits: Object.keys(drafts).length > 0,
    repricePast,
    setRepricePast,
    addModel,
    dropDraft,
    handleCostChange,
    handleSavePricing,
    handleResetPricing,
    repricing,
    handleRepriceModel,
  }
}
