import { useState, useCallback, useMemo, useEffect, useRef } from 'react'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import type { PricingEntry, PricingSource } from '../types.ts'

export type PriceField = 'input' | 'output' | 'cacheRead' | 'cacheWrite'
type PriceDraft = Record<PriceField, string>
/** A listed row; source 'new' marks an unsaved model added by name. */
export type Model = { name: string } & Omit<PricingEntry, 'source'> & { source: PricingSource | 'new' }

/** The model's current effective flat rate as editable strings. */
function draftFrom(model?: PricingEntry): PriceDraft {
  if (!model) return { input: '', output: '', cacheRead: '', cacheWrite: '' }
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
  const order = ['user', 'yaml']
  for (const name of ['openrouter', 'litellm']) {
    if (seen.size === 0 || seen.has(name)) order.push(name)
  }
  for (const name of [...seen].sort()) {
    if (!order.includes(name)) order.push(name)
  }
  return order
}

export function usePricingData() {
  const {
    setConfigStatus,
    setError,
    showToast,
    pricingData,
    setPricingData,
  } = useApp()

  const [pricingSearch, setPricingSearch] = useState('')
  const [drafts, setDrafts] = useState<Record<string, PriceDraft>>({})
  const [repricePast, setRepricePast] = useState(true)
  // Monotonic id: only the latest pricing response may update state, so a
  // slow earlier fetch (or the save refetch) can't overwrite newer data.
  const pricingSeqRef = useRef(0)

  useEffect(() => {
    const controller = new AbortController()
    const seq = ++pricingSeqRef.current

    async function fetchPricing() {
      try {
        const response = await fetch('/pricing', {
          signal: controller.signal,
        })
        if (response.ok && seq === pricingSeqRef.current) {
          const data = await response.json()
          setPricingData(data)
        }
      } catch (err) {
        if (!(err instanceof DOMException && err.name === 'AbortError')) {
          console.error('Failed to load pricing:', err)
        }
      }
    }

    void fetchPricing()
    return () => controller.abort()
  }, [setPricingData])

  const filteredPricingModels = useMemo(() => {
    if (!pricingData) return []
    const search = pricingSearch.toLowerCase()
    const models: Model[] = []
    for (const [name, data] of Object.entries(pricingData)) {
      if (search && !name.toLowerCase().includes(search)) continue
      models.push({ name, ...data })
    }
    // Custom overrides first, then server config, then sources in resolution priority order.
    const order = pricingSourceOrder(models.map((m) => m.source))
    const rank = (source: string) => {
      const i = order.indexOf(source)
      return i === -1 ? order.length : i
    }
    models.sort((a, b) => rank(a.source) - rank(b.source) || a.name.localeCompare(b.name))
    return models
  }, [pricingData, pricingSearch])

  /** Draft-only rows: models added by name that no source prices yet. */
  const newModels = useMemo(
    () => Object.keys(drafts)
      .filter((name) => !pricingData?.[name])
      .map((name): Model => ({ name, input: 0, output: 0, cache_read: 0, cache_write: null, source: 'new' })),
    [drafts, pricingData],
  )

  const handleCostChange = useCallback((model: Model, field: PriceField, val: string) => {
    setDrafts((prev) => {
      // The first edit prefills the model's effective prices so the user
      // edits the full flat rate, not one field of it.
      const draft = prev[model.name] ?? draftFrom(pricingData?.[model.name])
      return { ...prev, [model.name]: { ...draft, [field]: val } }
    })
  }, [pricingData])

  const dropDraft = useCallback((model: string) => {
    setDrafts((prev) => {
      const next = { ...prev }
      delete next[model]
      return next
    })
  }, [])

  /** Start a draft for a model by name. Returns false when the model is
   *  already listed (the search is narrowed to it instead). */
  const addModel = useCallback((name: string) => {
    // The server stores override names lowercased, so match and key the same way.
    const model = name.trim().toLowerCase()
    if (!model) return true
    const existing = Object.keys(pricingData ?? {}).find((key) => key.toLowerCase() === model)
    if (existing) {
      setPricingSearch(existing)
      return false
    }
    setDrafts((prev) => (prev[model] ? prev : { ...prev, [model]: draftFrom() }))
    return true
  }, [pricingData])

  const refetchPricing = useCallback(async () => {
    try {
      const seq = ++pricingSeqRef.current
      const pricingResp = await fetch('/pricing')
      if (pricingResp.ok && seq === pricingSeqRef.current) {
        setPricingData(await pricingResp.json())
      }
    } catch {
      /* non-critical */
    }
  }, [setPricingData])

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

  return {
    pricingSearch,
    setPricingSearch,
    filteredPricingModels,
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
  }
}
