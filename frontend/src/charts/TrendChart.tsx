import { useEffect, useMemo, useState } from 'react'
import type { DailyUsage } from '../types'
import { fillGaps, formatCost, formatModelName, formatNumber, value, resolveTimezone, wallClockMs, getTimezoneOffset } from '../utils'
import { getModelColor } from '../model-badge'
import { fillPeriodGaps } from '../periodGaps'
import { describePeriod, periodLabelText } from '../periodLabels'
import { useApp } from '../contexts/AppContext'
import { ChartTooltip, TooltipRow, TooltipDivider } from './ChartTooltip'
import { getLang, LANG_LOCALES, t } from '../i18n/index.ts'

type ModelBucketRow = {
  dimension: string
  period: string
  total_tokens: number | null
  total_cost_usd: number | null
  requests: number | null
}

/** How many models get their own colour; the rest are lumped into "Other". */
const MODEL_LEGEND_MAX = 5

export function TrendChart({
  data,
  title,
  theme = 'light',
  granularity = 'day',
  periodCount,
  showDots = true,
  filterParams = {},
}: {
  data: DailyUsage[],
  title: string,
  theme?: 'light' | 'dark',
  granularity?: 'hour' | 'day',
  /** Buckets to show, anchored to now. Without it the plot spans the data. */
  periodCount?: number,
  showDots?: boolean,
  filterParams?: {
    provider?: string
    model?: string | null
    client_source?: string | null
    device_id?: string | null
    since?: string | null
    until?: string | null
  }
}) {
  const { timezone } = useApp()
  const tz = resolveTimezone(timezone)
  const [metric, setMetric] = useState<'tokens' | 'cost' | 'models'>('tokens');
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [showCacheHit, setShowCacheHit] = useState(false);
  const filled = useMemo(
    () => (periodCount === undefined
      ? fillPeriodGaps(data, granularity)
      : fillGaps(data, granularity, periodCount, tz)),
    [data, granularity, periodCount, tz],
  );
  const maxTokens = Math.max(...filled.map(x => value(x.total_tokens)), 1);
  const maxCost = Math.max(...filled.map(x => value(x.total_cost_usd)), 0.001);
  const maxRequests = Math.max(...filled.map(x => value(x.requests)), 1);
  // cache_creation_tokens is a disjoint bucket on top of prompt_tokens (Anthropic
  // semantics), so it must be added to the denominator for a true hit rate.
  const hitRates = filled.map(d => {
    const totalInput = value(d.prompt_tokens) + value(d.cache_creation_tokens);
    return totalInput > 0 ? (value(d.cached_tokens) / totalInput) * 100 : 0;
  });
  const maxHitRate = Math.max(...hitRates, 100);
  const paddingX = 60;
  const chartWidth = 1000 - (paddingX * 2);

  const hoveredData = hoveredIdx !== null ? filled[hoveredIdx] : null;
  // The tooltip follows the hovered bucket, but never leaves the panel.
  const tooltipLeft = hoveredIdx === null
    ? 50
    : (paddingX + ((hoveredIdx + 0.5) / filled.length) * chartWidth) / 10;
  // Model split: one row per (model, bucket) from /usage/daily-by-dimension.
  const tzOffset = getTimezoneOffset(tz);
  const [modelRows, setModelRows] = useState<ModelBucketRow[]>([]);
  useEffect(() => {
    if (metric !== 'models') return;
    const controller = new AbortController();
    async function fetchModelRows() {
      try {
        const url = new URL('/usage/daily-by-dimension', window.location.origin);
        url.searchParams.set('dimension', 'model');
        url.searchParams.set('granularity', granularity);
        if (filterParams.provider) url.searchParams.set('provider', filterParams.provider);
        if (filterParams.model) url.searchParams.set('model', filterParams.model);
        if (filterParams.client_source) url.searchParams.set('client_source', filterParams.client_source);
        if (filterParams.device_id) url.searchParams.set('device_id', filterParams.device_id);
        if (filterParams.since) url.searchParams.set('since', filterParams.since);
        if (filterParams.until) url.searchParams.set('until', filterParams.until);
        url.searchParams.set('tz_offset', tzOffset);

        const res = await fetch(url.toString(), { signal: controller.signal });
        if (res.ok) {
          const rows = await res.json();
          if (!controller.signal.aborted) setModelRows(rows);
        }
      } catch {
        // Ignore abort errors
      }
    }
    fetchModelRows();
    return () => {
      controller.abort();
      // The rows belong to the old filters until the new ones land.
      setModelRows([]);
    };
  }, [metric, filterParams, granularity, tzOffset]);

  const modelBuckets = useMemo(() => {
    const map = new Map<string, { model: string; tokens: number; cost: number; requests: number }[]>();
    for (const row of modelRows) {
      const list = map.get(row.period) ?? [];
      list.push({
        model: row.dimension,
        tokens: value(row.total_tokens),
        cost: value(row.total_cost_usd),
        requests: value(row.requests),
      });
      map.set(row.period, list);
    }
    for (const list of map.values()) list.sort((a, b) => b.tokens - a.tokens);
    return map;
  }, [modelRows]);

  // The models that get their own colour, biggest first.
  const modelTotals = useMemo(() => {
    const totals = new Map<string, number>();
    for (const row of modelRows) {
      totals.set(row.dimension, (totals.get(row.dimension) ?? 0) + value(row.total_tokens));
    }
    return [...totals.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([model, tokens]) => ({ model, tokens }));
  }, [modelRows]);
  const legendModels = modelTotals.slice(0, MODEL_LEGEND_MAX);
  const otherModelTokens = modelTotals.slice(MODEL_LEGEND_MAX).reduce((sum, m) => sum + m.tokens, 0);
  const modelColor = (model: string) => getModelColor(model, theme);
  const nowWallMs = wallClockMs(new Date(), tz);
  const hoveredPeriod = hoveredData ? describePeriod(hoveredData.period, granularity, nowWallMs, LANG_LOCALES[getLang()]) : null;
  const hoveredPeriodLabel = periodLabelText(hoveredPeriod);
  const hCached = hoveredData ? value(hoveredData.cached_tokens) : 0;
  // Cache-write tokens are fresh input (billed differently from cache-read),
  // so they're folded into the Input bucket rather than the Cached bucket --
  // otherwise Input+Cached+Output would undercount total_tokens.
  const hInput = hoveredData ? Math.max(0, value(hoveredData.prompt_tokens) - hCached) + value(hoveredData.cache_creation_tokens) : 0;
  const hOutput = hoveredData ? value(hoveredData.completion_tokens) : 0;
  const hTotalInput = hoveredData ? value(hoveredData.prompt_tokens) + value(hoveredData.cache_creation_tokens) : 0;
  const hHitRate = hTotalInput > 0 ? (hCached / hTotalInput) * 100 : 0;

  return (
    <div className="widget" style={{ minHeight: '400px', width: '100%', position: 'relative' }}>
      <div className="widget-header" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
        <span>📈 {title}</span>
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '16px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
            {metric === 'models' ? (
              <>
                {legendModels.map(m => (
                  <div key={m.model} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <div style={{ width: '12px', height: '12px', background: modelColor(m.model), borderRadius: '2px' }} />
                    <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{formatModelName(m.model)}</span>
                  </div>
                ))}
                {otherModelTokens > 0 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <div style={{ width: '12px', height: '12px', background: 'var(--text-muted)', borderRadius: '2px' }} />
                    <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{t('Other')}</span>
                  </div>
                )}
              </>
            ) : metric === 'tokens' ? (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div style={{ width: '12px', height: '12px', background: '#94a3b8', borderRadius: '2px' }} />
                  <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{t('Input')}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div style={{ width: '12px', height: '12px', background: 'var(--color-green)', borderRadius: '2px' }} />
                  <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{t('Cached')}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div style={{ width: '12px', height: '12px', background: 'var(--color-blue)', borderRadius: '2px' }} />
                  <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{t('Output')}</span>
                </div>
              </>
            ) : (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div style={{ width: '12px', height: '12px', background: '#d97706', borderRadius: '2px' }} />
                  <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{t('Input Cost')}</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div style={{ width: '12px', height: '12px', background: '#a855f7', borderRadius: '2px' }} />
                  <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{t('Output Cost')}</span>
                </div>
              </>
            )}
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div style={{ width: '12px', height: '3px', background: 'var(--color-pink)', borderRadius: '2px' }} />
                  <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{t('Requests')}</span>
                </div>
                <button
                  type="button"
                  aria-pressed={showCacheHit}
                  onClick={() => setShowCacheHit(!showCacheHit)}
                  style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', opacity: showCacheHit ? 1 : 0.4, background: 'none', border: 'none', padding: 0, font: 'inherit' }}
                >
                   <div style={{ width: '12px', height: '3px', background: 'var(--color-green)', borderRadius: '2px' }} />
                   <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{t('Cache Hit Rate')}</span>
                 </button>
          </div>
          <div className="tab-toggle">
            <button
              className={`tab-toggle-btn ${metric === 'tokens' ? 'active' : ''}`}
              onClick={() => setMetric('tokens')}
            >{t('Tokens')}</button>
            <button
              className={`tab-toggle-btn ${metric === 'cost' ? 'active' : ''}`}
              onClick={() => setMetric('cost')}
            >{t('Cost')}</button>
            <button
              className={`tab-toggle-btn ${metric === 'models' ? 'active' : ''}`}
              onClick={() => { setModelRows([]); setMetric('models'); }}
            >{t('Models')}</button>
          </div>
        </div>
      </div>
      <div style={{
        flex: 1,
        padding: '20px 0',
        height: '280px',
        position: 'relative',
        display: 'flex',
        flexDirection: 'column'
      }}>
        {filled.length === 0 ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>{t('No trend data available')}</div>
        ) : (
          <>
            {hoveredIdx !== null && hoveredData && (
              <ChartTooltip left={`${Math.min(Math.max(tooltipLeft, 12), 88)}%`}>
                <div style={{ fontWeight: 600, marginBottom: '8px', borderBottom: '1px solid var(--border-color)', paddingBottom: '4px', fontSize: '13px' }}>
                  {hoveredPeriodLabel ?? hoveredData.period}
                </div>
                {metric === 'models' ? (
                  <>
                    {(modelBuckets.get(hoveredData.period) ?? []).slice(0, MODEL_LEGEND_MAX).map(m => (
                      <TooltipRow key={m.model} label={formatModelName(m.model)} labelColor={modelColor(m.model)}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ fontWeight: 600 }}>{formatNumber(m.tokens)}</span>
                          <span style={{ color: 'var(--text-muted)' }}>{formatCost(m.cost, 4)}</span>
                        </div>
                      </TooltipRow>
                    ))}
                    <TooltipDivider />
                    <TooltipRow label={t('Total Tokens:')}>
                      <span style={{ fontWeight: 800 }}>{formatNumber(value(hoveredData.total_tokens))}</span>
                    </TooltipRow>
                    {hoveredData.total_cost_usd !== null && value(hoveredData.total_cost_usd) > 0 && (
                      <TooltipRow label={t('Est. Cost:')} labelColor="#f472b6">
                        <span style={{ fontWeight: 800, color: '#f472b6' }}>{formatCost(hoveredData.total_cost_usd, 4)}</span>
                      </TooltipRow>
                    )}
                  </>
                ) : metric === 'tokens' ? (
                  <>
                    <TooltipRow label={t('Input:')} labelColor="#94a3b8">
                      <span style={{ fontWeight: 600 }}>{formatNumber(hInput)}</span>
                    </TooltipRow>
                    <TooltipRow label={t('Cached:')} labelColor="var(--color-green)">
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        {hTotalInput > 0 && (
                          <span style={{ fontSize: '10px', color: 'var(--color-green)', opacity: 0.8 }}>
                            ({hHitRate.toFixed(1)}%)
                          </span>
                        )}
                        <span style={{ fontWeight: 600 }}>{formatNumber(hCached)}</span>
                      </div>
                    </TooltipRow>
                    <TooltipRow label={t('Output:')} labelColor="var(--color-blue)">
                      <span style={{ fontWeight: 600 }}>{formatNumber(hOutput)}</span>
                    </TooltipRow>
                    <TooltipDivider />
                    <TooltipRow label={t('Total Tokens:')}>
                      <span style={{ fontWeight: 800 }}>{formatNumber(value(hoveredData.total_tokens))}</span>
                    </TooltipRow>
                    {hoveredData.total_cost_usd !== null && value(hoveredData.total_cost_usd) > 0 && (
                      <TooltipRow label={t('Est. Cost:')} labelColor="#f472b6">
                        <span style={{ fontWeight: 800, color: '#f472b6' }}>{formatCost(hoveredData.total_cost_usd, 4)}</span>
                      </TooltipRow>
                    )}
                  </>
                ) : metric === 'cost' ? (
                  <>
                    <TooltipRow label={t('Input Cost:')} labelColor="#d97706">
                      <span style={{ fontWeight: 600 }}>{formatCost(hoveredData.input_cost_usd, 4)}</span>
                    </TooltipRow>
                    <TooltipRow label={t('Output Cost:')} labelColor="#a855f7">
                      <span style={{ fontWeight: 600 }}>{formatCost(hoveredData.output_cost_usd, 4)}</span>
                    </TooltipRow>
                    <TooltipDivider />
                    <TooltipRow label={t('Total Cost:')}>
                      <span style={{ fontWeight: 800 }}>{formatCost(hoveredData.total_cost_usd, 4)}</span>
                    </TooltipRow>
                    <TooltipRow label={t('Tokens:')}>
                      <span style={{ fontWeight: 600 }}>{formatNumber(value(hoveredData.total_tokens))}</span>
                    </TooltipRow>
                  </>
                ) : null}
                <TooltipRow label={t('Requests:')} labelColor="var(--color-pink)">
                  <span style={{ fontWeight: 600, color: 'var(--color-pink)' }}>{formatNumber(hoveredData.requests)}</span>
                </TooltipRow>
                {hTotalInput > 0 && (
                  <TooltipRow label={t('Cache Hit Rate:')} labelColor="var(--color-green)">
                    <span style={{ fontWeight: 600, color: 'var(--color-green)' }}>{hHitRate.toFixed(1)}%</span>
                  </TooltipRow>
                )}
              </ChartTooltip>
            )}

            <svg
              viewBox="0 0 1000 200"
              preserveAspectRatio="none"
              style={{ width: '100%', height: '220px', overflow: 'visible' }}
            >
              <defs>
                <linearGradient id="grad-tokens-input" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#94a3b8" stopOpacity="0.85" />
                  <stop offset="100%" stopColor="#94a3b8" stopOpacity="0.3" />
                </linearGradient>
                <linearGradient id="grad-tokens-cached" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-green)" stopOpacity="0.85" />
                  <stop offset="100%" stopColor="var(--color-green)" stopOpacity="0.3" />
                </linearGradient>
                <linearGradient id="grad-tokens-output" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--color-blue)" stopOpacity="0.85" />
                  <stop offset="100%" stopColor="var(--color-blue)" stopOpacity="0.3" />
                </linearGradient>
                <linearGradient id="grad-cost-input" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#d97706" stopOpacity="0.85" />
                  <stop offset="100%" stopColor="#d97706" stopOpacity="0.3" />
                </linearGradient>
                <linearGradient id="grad-cost-output" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#a855f7" stopOpacity="0.85" />
                  <stop offset="100%" stopColor="#a855f7" stopOpacity="0.3" />
                </linearGradient>
              </defs>
              {[0, 0.25, 0.5, 0.75, 1].map(tick => (
                <line
                  key={tick}
                  x1="0" y1={200 - tick * 200}
                  x2="1000" y2={200 - tick * 200}
                  stroke="var(--chart-grid)"
                  strokeWidth="1"
                />
              ))}

              {/* The hovered bucket, highlighted behind the bars */}
              {hoveredIdx !== null && (() => {
                const barSlot = chartWidth / filled.length;
                const cx = paddingX + (hoveredIdx + 0.5) / filled.length * chartWidth;
                return (
                  <rect
                    x={cx - barSlot / 2}
                    y={0}
                    width={Math.max(barSlot, 3)}
                    height={200}
                    fill="var(--text-muted)"
                    opacity={0.12}
                    style={{ pointerEvents: 'none' }}
                  />
                );
              })()}

              {/* Stacked Bars */}
              {(() => {
                const barSlot = chartWidth / filled.length;
                const barW = barSlot * 0.65;
                const radius = Math.min(3, barW / 4);

                const roundedRect = (x: number, y: number, w: number, h: number, rTop: number, rBot: number) => {
                  if (h <= 0) return '';
                  const t = Math.min(rTop, h / 2);
                  const b = Math.min(rBot, h / 2);
                  return `M ${x + t},${y} L ${x + w - t},${y} Q ${x + w},${y} ${x + w},${y + t} L ${x + w},${y + h - b} Q ${x + w},${y + h} ${x + w - b},${y + h} L ${x + b},${y + h} Q ${x},${y + h} ${x},${y + h - b} L ${x},${y + t} Q ${x},${y} ${x + t},${y} Z`;
                };

                return filled.map((d, i) => {
                  const cx = paddingX + (i + 0.5) / filled.length * chartWidth;
                  const x = cx - barW / 2;
                  const dimmed = hoveredIdx !== null && hoveredIdx !== i;

                  if (metric === 'models') {
                    const models = modelBuckets.get(d.period) ?? [];
                    if (models.length === 0) return null;

                    const segments = models.slice(0, MODEL_LEGEND_MAX);
                    const otherTokens = models.slice(MODEL_LEGEND_MAX).reduce((sum, m) => sum + m.tokens, 0);
                    const stack = otherTokens > 0
                      ? [...segments, { model: '', tokens: otherTokens, cost: 0, requests: 0 }]
                      : segments;

                    let yCursor = 200;
                    return (
                      <g key={i} style={{ opacity: dimmed ? 0.3 : 1, transition: 'opacity 0.2s' }}>
                        {stack.map((m, mi) => {
                          if (m.tokens <= 0) return null;
                          const h = (m.tokens / maxTokens) * 200;
                          const y = yCursor - h;
                          yCursor = y;
                          const isTop = mi === 0;
                          const isBottom = mi === stack.length - 1;
                          return (
                            <path
                              key={m.model || `other-${mi}`}
                              d={roundedRect(x, y, barW, h, isTop ? radius : 0, isBottom ? radius : 0)}
                              fill={m.model ? modelColor(m.model) : 'var(--text-muted)'}
                              opacity={0.85}
                            />
                          );
                        })}
                      </g>
                    );
                  } else if (metric === 'tokens') {
                    const cached = value(d.cached_tokens);
                    // Cache-write tokens fold into Input; see hInput above.
                    const input = Math.max(0, value(d.prompt_tokens) - cached) + value(d.cache_creation_tokens);
                    const output = value(d.completion_tokens);
                    const total = input + cached + output;
                    if (total === 0) return null;

                    const hOutput = (output / maxTokens) * 200;
                    const hCached = (cached / maxTokens) * 200;
                    const hInput = (input / maxTokens) * 200;

                    const yOutput = 200 - hOutput;
                    const yCached = yOutput - hCached;
                    const yInput = yCached - hInput;

                    const isTop = hInput > 0;
                    const hasMiddle = hCached > 0;
                    const hasBottom = hOutput > 0;

                    return (
                      <g key={i} style={{ opacity: dimmed ? 0.3 : 1, transition: 'opacity 0.2s' }}>
                        {hasBottom && (
                          <path d={roundedRect(x, yOutput, barW, hOutput, hasMiddle ? 0 : isTop ? 0 : radius, radius)} fill="url(#grad-tokens-output)" />
                        )}
                        {hasMiddle && (
                          <path d={roundedRect(x, yCached, barW, hCached, isTop ? 0 : radius, 0)} fill="url(#grad-tokens-cached)" />
                        )}
                        {isTop && (
                          <path d={roundedRect(x, yInput, barW, hInput, radius, 0)} fill="url(#grad-tokens-input)" />
                        )}
                      </g>
                    );
                  } else if (metric === 'cost') {
                    const inputCost = value(d.input_cost_usd);
                    const outputCost = value(d.output_cost_usd);
                    const total = inputCost + outputCost;
                    if (total === 0) return null;

                    const hOutput = (outputCost / maxCost) * 200;
                    const hInput = (inputCost / maxCost) * 200;

                    const yOutput = 200 - hOutput;
                    const yInput = yOutput - hInput;

                    const isTop = hInput > 0;
                    const hasBottom = hOutput > 0;

                    return (
                      <g key={i} style={{ opacity: dimmed ? 0.3 : 1, transition: 'opacity 0.2s' }}>
                        {hasBottom && (
                          <path d={roundedRect(x, yOutput, barW, hOutput, isTop ? 0 : radius, radius)} fill="url(#grad-cost-output)" />
                        )}
                        {isTop && (
                          <path d={roundedRect(x, yInput, barW, hInput, radius, 0)} fill="url(#grad-cost-input)" />
                        )}
                      </g>
                    );
                  }
                });
              })()}

              {/* Hover hitboxes */}
              {filled.map((_, i) => {
                const barSlot = chartWidth / filled.length;
                const cx = paddingX + (i + 0.5) / filled.length * chartWidth;
                return (
                  <rect
                    key={i}
                    x={cx - barSlot / 2} y={0}
                    width={barSlot} height={200}
                    fill="transparent"
                    style={{ cursor: 'pointer' }}
                    onMouseEnter={() => setHoveredIdx(i)}
                    onMouseLeave={() => setHoveredIdx(null)}
                  />
                );
              })}

              {/* Requests line */}
              {(() => {
                const points = filled.map((d, i) => {
                  const x = paddingX + (i + 0.5) / filled.length * chartWidth;
                  const y = 200 - (value(d.requests) / maxRequests) * 200;
                  return `${x},${y}`;
                }).join(' ');

                return (
                  <>
                    <polyline
                      points={points}
                      fill="none"
                      stroke="var(--color-pink)"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      style={{ pointerEvents: 'none', opacity: hoveredIdx === null ? 0.6 : 0.3 }}
                    />
                    {showDots && filled.map((d, i) => {
                      const x = paddingX + (i + 0.5) / filled.length * chartWidth;
                      const y = 200 - (value(d.requests) / maxRequests) * 200;
                      return (
                        <circle
                          key={i}
                          cx={x} cy={y} r={hoveredIdx === i ? "5" : "3"}
                          fill="var(--chart-dot-fill)"
                          stroke="var(--color-pink)"
                          strokeWidth={hoveredIdx === i ? "3" : "2"}
                          style={{ pointerEvents: 'none', transition: 'all 0.2s', opacity: hoveredIdx === null || hoveredIdx === i ? 0.6 : 0.3 }}
                        />
                      );
                    })}
                    {/* The hovered point stays marked even when the dots are off. */}
                    {hoveredIdx !== null && (() => {
                      const x = paddingX + (hoveredIdx + 0.5) / filled.length * chartWidth;
                      const y = 200 - (value(filled[hoveredIdx].requests) / maxRequests) * 200;
                      return (
                        <circle
                          cx={x} cy={y} r="5"
                          fill="var(--chart-dot-fill)"
                          stroke="var(--color-pink)"
                          strokeWidth="3"
                          style={{ pointerEvents: 'none' }}
                        />
                      );
                    })()}
                  </>
                );
              })()}
{showCacheHit && (
                <>
                  {(() => {
                    const points = filled.map((_, i) => {
                      const x = paddingX + (i + 0.5) / filled.length * chartWidth;
                      const rate = hitRates[i] || 0;
                      const y = 200 - (rate / maxHitRate) * 200;
                      return `${x},${y}`;
                    }).join(' ');

                    return (
                      <>
                        <polyline
                          points={points}
                          fill="none"
                          stroke="var(--color-green)"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          style={{ pointerEvents: 'none', opacity: hoveredIdx === null ? 0.7 : 0.3 }}
                        />
                        {showDots && filled.map((_, i) => {
                          const x = paddingX + (i + 0.5) / filled.length * chartWidth;
                          const rate = hitRates[i] || 0;
                          const y = 200 - (rate / maxHitRate) * 200;
                          return (
                            <circle
                              key={i}
                              cx={x} cy={y} r={hoveredIdx === i ? "4.5" : "2.5"}
                              fill="var(--chart-dot-fill)"
                              stroke="var(--color-green)"
                              strokeWidth={hoveredIdx === i ? "3" : "1.5"}
                              style={{ pointerEvents: 'none', transition: 'all 0.2s', opacity: hoveredIdx === null || hoveredIdx === i ? 0.7 : 0.2 }}
                            />
                          );
                        })}
                      </>
                    );
                  })()}
                </>
              )}

              {/* A crosshair keeps the hovered bucket findable when the bars
                  are only a couple of pixels wide. */}
              {hoveredIdx !== null && (() => {
                const cx = paddingX + (hoveredIdx + 0.5) / filled.length * chartWidth;
                return (
                  <line
                    x1={cx} y1={0} x2={cx} y2={200}
                    stroke="var(--text-secondary)"
                    strokeWidth={1}
                    opacity={0.5}
                    style={{ pointerEvents: 'none' }}
                  />
                );
              })()}

                {/* Right Y-axis labels */}
                {showCacheHit && [0, 25, 50, 75, 100].map(tick => (
                  <text
                    key={tick}
                    x={1000 - paddingX + 5}
                    y={200 - (tick / maxHitRate) * 200 + 3}
                    fill="var(--text-muted)"
                    fontSize="9"
                    textAnchor="start"
                  >
                    {tick}%
                  </text>
                ))}
             </svg>

            <div style={{
              display: 'flex',
              justifyContent: 'space-between',
              marginTop: '20px',
              borderTop: '1px solid var(--chart-footer-border)',
              paddingTop: '10px',
              paddingLeft: `${(paddingX / 1000) * 100}%`,
              paddingRight: `${(paddingX / 1000) * 100}%`
            }}>
              {filled.map((d, i) => {
                if (filled.length > 12 && i % Math.ceil(filled.length / 12) !== 0 && i !== filled.length - 1) {
                  return null;
                }
                const label = d.period.includes(':')
                  ? d.period.split(' ')[1]
                  : d.period.split('-').slice(1).join('/');
                return (
                  <div key={d.period} style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
                    {label}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
