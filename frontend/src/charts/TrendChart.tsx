import { appendModels } from '../modelFilter'
import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import type { DailyUsage, PeriodGranularity } from '../types'
import { fillGaps, formatCost, formatModelName, formatNumber, value, resolveTimezone, wallClockMs, getTimezoneOffset, modelFamily, getModelIcon } from '../utils'
import { fillPeriodGaps } from '../periodGaps'
import { describePeriod, periodLabelText } from '../periodLabels'
import { useApp } from '../contexts/AppContext'
import { ChartTooltip, TooltipRow, TooltipDivider } from './ChartTooltip'
import { TREND_NEUTRAL, trendSeriesColors } from './trendColors'
import { sortTrendRows, trendTooltipX } from './trendTooltip'
import { trendDotScale } from './trendScale'
import { getLang, LANG_LOCALES, t } from '../i18n/index.ts'

type ModelBucketRow = {
  dimension: string
  period: string
  total_tokens: number | null
  total_cost_usd: number | null
  requests: number | null
}

/** Above this many models, group the chart by family. */
const MODEL_LEGEND_MAX = 8
/** Stable default: the model fetch keys on the filter object's identity. */
const NO_FILTERS = {}

export function TrendChart({
  data,
  title,
  granularity = 'day',
  periodCount,
  showDots = true,
  filterParams = NO_FILTERS,
}: {
  data: DailyUsage[],
  title: string,
  granularity?: PeriodGranularity,
  /** Buckets to show, anchored to now. Without it the plot spans the data. */
  periodCount?: number,
  showDots?: boolean,
  filterParams?: {
    provider?: string
    model?: string[] | null
    client_source?: string | null
    device_id?: string | null
    since?: string | null
  }
}) {
  const { timezone, theme } = useApp()
  const tz = resolveTimezone(timezone)
  const [metric, setMetric] = useState<'tokens' | 'cost'>('tokens');
  const [showRequests, setShowRequests] = useState(true);
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const chartAreaRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const tooltipXRef = useRef(268);
  const tooltipWidthRef = useRef(520);
  const chartId = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const hoverBandId = `trend-hover-band-${chartId}`;
  const crosshairId = `trend-crosshair-${chartId}`;
  // The plot stretches its viewBox to the panel, so the request dots need the
  // panel width to be counter-scaled back into circles.
  const [plotWidth, setPlotWidth] = useState(0);
  const plotRef = useCallback((node: SVGSVGElement | null) => {
    if (!node) return;
    const observer = new ResizeObserver(() => setPlotWidth(node.getBoundingClientRect().width));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const dotScale = trendDotScale(plotWidth);
  const filled = useMemo(
    () => (periodCount === undefined
      ? fillPeriodGaps(data, granularity)
      : fillGaps(data, granularity, periodCount, tz)),
    [data, granularity, periodCount, tz],
  );
  const maxTokens = Math.max(...filled.map(x => value(x.total_tokens)), 1);
  const maxCost = Math.max(...filled.map(x => value(x.total_cost_usd)), 0.001);
  const maxRequests = Math.max(...filled.map(x => value(x.requests)), 1);
  const paddingX = 60;
  const chartWidth = 1000 - (paddingX * 2);

  const hoveredData = hoveredIdx !== null ? filled[hoveredIdx] : null;
  const hoverColor = metric === 'cost' ? 'var(--color-blue)' : 'var(--color-yellow)';
  // Follow the cursor without re-rendering the chart: only a bucket change
  // updates React state, while the tooltip moves on its own transform.
  // Near the edges the tooltip slows down and drifts past the plot instead of
  // pinning itself a full tooltip-width away from the pointer.
  const placeTooltip = (rect: DOMRect, measure = false) => {
    const tooltip = tooltipRef.current;
    if (!tooltip || rect.width === 0) return;
    if (measure) tooltipWidthRef.current = tooltip.offsetWidth || tooltipWidthRef.current;
    const overflow = Math.min(
      Math.max(0, rect.left - 8),
      Math.max(0, window.innerWidth - rect.right - 8),
    );
    const x = trendTooltipX(tooltipXRef.current, rect.width, tooltipWidthRef.current, overflow);
    tooltip.style.transform = `translate3d(${x}px, 0, 0) translateX(-50%)`;
  };
  const handlePointer = (e: PointerEvent<SVGRectElement>) => {
    const rect = chartAreaRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    const ratio = (((e.clientX - rect.left) / rect.width) * 1000 - paddingX) / chartWidth;
    const idx = Math.min(filled.length - 1, Math.max(0, Math.floor(ratio * filled.length)));
    tooltipXRef.current = e.clientX - rect.left;
    setHoveredIdx(prev => (prev === idx ? prev : idx));
    placeTooltip(rect);
  };
  const clearHover = () => setHoveredIdx(null);
  // The tooltip mounts after the first pointer event; measure it and place it
  // before paint. Re-measuring on render keeps the clamp honest when the
  // bucket content changes width.
  useLayoutEffect(() => {
    if (hoveredIdx === null) return;
    const rect = chartAreaRef.current?.getBoundingClientRect();
    if (rect) placeTooltip(rect, true);
  });
  // Model split: one row per (model, bucket) from /usage/daily-by-dimension.
  const tzOffset = getTimezoneOffset(tz);
  const [modelRows, setModelRows] = useState<ModelBucketRow[]>([]);
  const [modelError, setModelError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    async function fetchModelRows() {
      try {
        const url = new URL('/usage/daily-by-dimension', window.location.origin);
        url.searchParams.set('dimension', 'model');
        url.searchParams.set('granularity', granularity);
        if (filterParams.provider) url.searchParams.set('provider', filterParams.provider);
        appendModels(url.searchParams, filterParams.model);
        if (filterParams.client_source) url.searchParams.set('client_source', filterParams.client_source);
        if (filterParams.device_id) url.searchParams.set('device_id', filterParams.device_id);
        if (filterParams.since) url.searchParams.set('since', filterParams.since);
        url.searchParams.set('tz_offset', tzOffset);

        const res = await fetch(url.toString(), { signal: controller.signal });
        if (!res.ok) throw new Error();
        const rows = await res.json();
        if (!controller.signal.aborted) {
          setModelRows(rows);
          setModelError(false);
        }
      } catch {
        if (!controller.signal.aborted) setModelError(true);
      }
    }
    fetchModelRows();
    return () => {
      controller.abort();
      // The rows belong to the old filters until the new ones land.
      setModelRows([]);
      setModelError(false);
    };
  }, [filterParams, granularity, tzOffset]);

  const modelBuckets = useMemo(() => {
    const map = new Map<string, Map<string, { tokens: number; cost: number; requests: number }>>();
    const visiblePeriods = new Set(filled.map(row => row.period));
    for (const row of modelRows) {
      if (!visiblePeriods.has(row.period)) continue;
      const bucket = map.get(row.period) ?? new Map();
      const stat = bucket.get(row.dimension) ?? { tokens: 0, cost: 0, requests: 0 };
      stat.tokens += value(row.total_tokens);
      stat.cost += value(row.total_cost_usd);
      stat.requests += value(row.requests);
      bucket.set(row.dimension, stat);
      map.set(row.period, bucket);
    }
    return map;
  }, [modelRows, filled]);

  // Biggest first by tokens, so the legend order and colours stay put when
  // switching between the token and cost views.
  const modelTotals = useMemo(() => {
    const totals = new Map<string, number>();
    for (const bucket of modelBuckets.values()) {
      for (const [model, stat] of bucket) totals.set(model, (totals.get(model) ?? 0) + stat.tokens);
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [modelBuckets]);
  const familyMode = modelTotals.length > MODEL_LEGEND_MAX;
  const legendModels = useMemo(() => {
    const entries = new Map<string, { model: string; label: string; iconModel: string; tokens: number }>();
    for (const [model, tokens] of modelTotals) {
      const family = modelFamily(model);
      const key = familyMode ? family.id : model;
      const entry = entries.get(key) ?? { model: key, label: familyMode ? family.label : formatModelName(model), iconModel: model, tokens: 0 };
      entry.tokens += tokens;
      entries.set(key, entry);
    }
    return [...entries.values()].sort((a, b) => b.tokens - a.tokens || a.model.localeCompare(b.model));
  }, [modelTotals, familyMode]);
  // One colour per series: a model in model mode, a family in family mode.
  // The biggest series keeps its brand colour; the others step aside when
  // their brand colour is too close to a colour already on the chart.
  const modelColors = useMemo(
    () => trendSeriesColors(legendModels.map(m => ({ key: m.model, model: m.iconModel })), theme),
    [legendModels, theme],
  );
  // Keep the same segment order in every bucket, regardless of metric.
  const bucketSegments = (period: string) => {
    const bucket = modelBuckets.get(period);
    if (!bucket) return [];
    return legendModels.flatMap(entry => {
      const models = [...bucket.entries()].filter(([model]) => familyMode ? modelFamily(model).id === entry.model : model === entry.model)
        .map(([model, stat]) => ({ model, ...stat }));
      const stat = models.reduce((sum, m) => ({ tokens: sum.tokens + m.tokens, cost: sum.cost + m.cost, requests: sum.requests + m.requests }), { tokens: 0, cost: 0, requests: 0 });
      return stat.tokens > 0 || stat.cost > 0 ? [{ model: entry.model, label: entry.label, models, ...stat }] : [];
    });
  };
  const modelColor = (model: string) => modelColors.get(model) ?? TREND_NEUTRAL[theme];
  const nowWallMs = wallClockMs(new Date(), tz);
  const hoveredPeriod = hoveredData ? describePeriod(hoveredData.period, granularity, nowWallMs, LANG_LOCALES[getLang()]) : null;
  const hoveredPeriodLabel = periodLabelText(hoveredPeriod);

  return (
    <div className="widget" style={{ minHeight: '400px', width: '100%', position: 'relative' }}>
      <div className="widget-header" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
        <span>📈 {title}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <button
            type="button"
            aria-pressed={showRequests}
            aria-label={t(showRequests ? 'Hide requests' : 'Show requests')}
            title={t(showRequests ? 'Hide requests' : 'Show requests')}
            onClick={() => setShowRequests(visible => !visible)}
            style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: 0, border: 0, background: 'none', cursor: 'pointer', opacity: showRequests ? 1 : 0.4 }}
          >
            <div style={{ width: '12px', height: '3px', background: 'var(--color-pink)', borderRadius: '2px' }} />
            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)', textDecoration: showRequests ? 'none' : 'line-through' }}>{t('Requests')}</span>
          </button>
          <div className="tab-toggle">
            <button
              className={`tab-toggle-btn ${metric === 'tokens' ? 'active' : ''}`}
              onClick={() => setMetric('tokens')}
            >{t('Tokens')}</button>
            <button
              className={`tab-toggle-btn ${metric === 'cost' ? 'active' : ''}`}
              onClick={() => setMetric('cost')}
            >{t('Cost')}</button>
          </div>
        </div>
      </div>
      <div ref={chartAreaRef} style={{
        flex: 1,
        padding: '20px 0',
        minHeight: '280px',
        position: 'relative',
        display: 'flex',
        flexDirection: 'column'
      }}>
        {modelError && <div role="alert" style={{ textAlign: 'center', color: 'var(--badge-error-text)' }}>{t('Failed to load model breakdown')}</div>}
        {filled.length === 0 ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>{t('No trend data available')}</div>
        ) : (
          <>
            {hoveredIdx !== null && hoveredData && (
              <ChartTooltip
                ref={tooltipRef}
                left="0"
                style={{
                  width: 'max-content',
                  maxWidth: 'min(520px, calc(100% - 16px))',
                  fontVariantNumeric: 'tabular-nums',
                  transform: 'translate3d(268px, 0, 0) translateX(-50%)',
                  willChange: 'transform',
                  animation: 'trend-tooltip-in 120ms ease-out',
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: '8px', borderBottom: '1px solid var(--border-color)', paddingBottom: '4px', fontSize: '13px' }}>
                  {hoveredPeriodLabel ?? hoveredData.period}
                </div>
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(0, 1fr) auto auto',
                  columnGap: '12px',
                  rowGap: '4px',
                  alignItems: 'baseline',
                  marginBottom: '4px',
                }}>
                  {sortTrendRows(bucketSegments(hoveredData.period), metric).map(m => {
                    const primary = metric === 'cost' ? formatCost(m.cost) : formatNumber(m.tokens);
                    const secondary = metric === 'cost' ? formatNumber(m.tokens) : formatCost(m.cost);
                    return (
                      <Fragment key={m.model || 'other'}>
                        <span style={{
                          color: modelColor(m.model),
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}>
                          {familyMode ? t(m.label) : m.label}
                        </span>
                        <span style={{ fontWeight: 600, textAlign: 'right' }}>{primary}</span>
                        <span style={{ color: 'var(--text-muted)', textAlign: 'right' }}>{secondary}</span>
                        {familyMode && sortTrendRows(m.models, metric).map(model => (
                          <Fragment key={model.model}>
                            <span style={{ paddingLeft: '12px', color: 'var(--text-muted)' }}>{formatModelName(model.model)}</span>
                            <span style={{ textAlign: 'right' }}>{metric === 'cost' ? formatCost(model.cost) : formatNumber(model.tokens)}</span>
                            <span style={{ color: 'var(--text-muted)', textAlign: 'right' }}>{metric === 'cost' ? formatNumber(model.tokens) : formatCost(model.cost)}</span>
                          </Fragment>
                        ))}
                      </Fragment>
                    );
                  })}
                </div>
                <TooltipDivider />
                <TooltipRow label={metric === 'cost' ? t('Total Cost:') : t('Total Tokens:')}>
                  <span style={{ fontWeight: 800 }}>
                    {metric === 'cost' ? formatCost(hoveredData.total_cost_usd) : formatNumber(value(hoveredData.total_tokens))}
                  </span>
                </TooltipRow>
                <TooltipRow label={metric === 'cost' ? t('Tokens:') : t('Est. Cost:')} labelColor="#f472b6">
                  <span style={{ fontWeight: 800, color: '#f472b6' }}>
                    {metric === 'cost' ? formatNumber(value(hoveredData.total_tokens)) : formatCost(hoveredData.total_cost_usd)}
                  </span>
                </TooltipRow>
                {showRequests && <TooltipRow label={t('Requests:')} labelColor="var(--color-pink)">
                  <span style={{ fontWeight: 600, color: 'var(--color-pink)' }}>{formatNumber(hoveredData.requests)}</span>
                </TooltipRow>}
              </ChartTooltip>
            )}

            <svg
              ref={plotRef}
              viewBox="0 0 1000 200"
              preserveAspectRatio="none"
              style={{ width: '100%', height: '220px', overflow: 'visible' }}
            >
              <defs>
                <linearGradient id={hoverBandId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={hoverColor} stopOpacity="0.02" />
                  <stop offset="50%" stopColor={hoverColor} stopOpacity="0.14" />
                  <stop offset="100%" stopColor={hoverColor} stopOpacity="0.02" />
                </linearGradient>
                <linearGradient id={crosshairId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={hoverColor} stopOpacity="0.05" />
                  <stop offset="45%" stopColor={hoverColor} stopOpacity="0.75" />
                  <stop offset="100%" stopColor={hoverColor} stopOpacity="0.2" />
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
                  <g style={{
                    transform: `translateX(${cx}px)`,
                    transition: 'transform 160ms cubic-bezier(0.22, 1, 0.36, 1)',
                    pointerEvents: 'none',
                  }}>
                    <rect
                      x={-Math.max(barSlot, 3) / 2}
                      y={0}
                      width={Math.max(barSlot, 3)}
                      height={200}
                      rx={4}
                      fill={`url(#${hoverBandId})`}
                    />
                  </g>
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

                  const maxValue = metric === 'cost' ? maxCost : maxTokens;
                  const stack = bucketSegments(d.period)
                    .filter(m => (metric === 'cost' ? m.cost : m.tokens) > 0);
                  if (stack.length === 0) return null;

                  let yCursor = 200;
                  return (
                    <g
                      key={i}
                      style={{
                        opacity: dimmed ? 0.32 : 1,
                        transform: hoveredIdx === i ? 'scaleX(1.06)' : 'scaleX(1)',
                        transformBox: 'fill-box',
                        transformOrigin: 'center bottom',
                        transition: 'opacity 160ms ease, transform 160ms ease',
                        filter: hoveredIdx === i ? 'brightness(1.12)' : 'none',
                      }}
                    >
                      {stack.map((m, mi) => {
                        const segment = metric === 'cost' ? m.cost : m.tokens;
                        const h = (segment / maxValue) * 200;
                        const y = yCursor - h;
                        yCursor = y;
                        const isTop = mi === stack.length - 1;
                        const isBottom = mi === 0;
                        return (
                          <path
                            key={m.model || 'other'}
                            d={roundedRect(x, y, barW, h, isTop ? radius : 0, isBottom ? radius : 0)}
                            fill={modelColor(m.model)}
                            opacity={0.9}
                            stroke="var(--chart-dot-fill)"
                            strokeWidth={1}
                          />
                        );
                      })}
                    </g>
                  );
                });
              })()}

              {/* Requests line */}
              {showRequests && (() => {
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
                      style={{ pointerEvents: 'none', opacity: hoveredIdx === null ? 0.45 : 0.22, transition: 'opacity 160ms ease' }}
                    />
                    {showDots && filled.map((d, i) => {
                      const x = paddingX + (i + 0.5) / filled.length * chartWidth;
                      const y = 200 - (value(d.requests) / maxRequests) * 200;
                      return (
                        // The x scale cancels the stretch of the viewBox, so the
                        // dot stays a circle on any panel width.
                        <g key={i} style={{ transform: `translate(${x}px, ${y}px) scale(${dotScale}, 1)`, transformOrigin: '0 0', pointerEvents: 'none' }}>
                          <circle
                            cx={0} cy={0} r={hoveredIdx === i ? "5" : "3"}
                            fill="var(--chart-dot-fill)"
                            stroke="var(--color-pink)"
                            strokeWidth={hoveredIdx === i ? "3" : "2"}
                            style={{ transition: 'all 160ms ease', opacity: hoveredIdx === null || hoveredIdx === i ? 0.45 : 0.22 }}
                          />
                        </g>
                      );
                    })}
                    {/* The hovered point stays marked even when the dots are off. */}
                    {hoveredIdx !== null && hoveredData && (() => {
                      const x = paddingX + (hoveredIdx + 0.5) / filled.length * chartWidth;
                      const y = 200 - (value(filled[hoveredIdx].requests) / maxRequests) * 200;
                      return (
                        <g style={{
                          transform: `translate(${x}px, ${y}px) scale(${dotScale}, 1)`,
                          transformOrigin: '0 0',
                          transition: 'transform 160ms cubic-bezier(0.22, 1, 0.36, 1)',
                          pointerEvents: 'none',
                        }}>
                          <circle cx={0} cy={0} r="5" fill="var(--chart-dot-fill)" stroke="var(--color-pink)" strokeWidth="3" />
                          <circle cx={0} cy={0} r="9" fill="none" stroke="var(--color-pink)" strokeWidth="1.5" opacity="0.35" className="trend-hover-pulse" />
                        </g>
                      );
                    })()}
                  </>
                );
              })()}
              {/* A crosshair keeps the hovered bucket findable when the bars
                  are only a couple of pixels wide. */}
              {hoveredIdx !== null && (() => {
                const cx = paddingX + (hoveredIdx + 0.5) / filled.length * chartWidth;
                return (
                  <g style={{
                    transform: `translateX(${cx}px)`,
                    transition: 'transform 160ms cubic-bezier(0.22, 1, 0.36, 1)',
                    pointerEvents: 'none',
                  }}>
                    <line x1={0} y1={0} x2={0} y2={200} stroke={hoverColor} strokeWidth={6} opacity={0.08} />
                    <line x1={0} y1={0} x2={0} y2={200} stroke={`url(#${crosshairId})`} strokeWidth={1} />
                  </g>
                );
              })()}

              {/* One hit surface for the whole plot: bucket changes re-render,
                  pointer movement only moves the tooltip. */}
              <rect
                x={paddingX} y={0}
                width={chartWidth} height={200}
                fill="transparent"
                style={{ cursor: 'crosshair' }}
                onPointerEnter={handlePointer}
                onPointerMove={handlePointer}
                onPointerLeave={clearHover}
                onPointerCancel={clearHover}
                onPointerUp={e => { if (e.pointerType !== 'mouse') clearHover(); }}
              />
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

            <div style={{
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '16px',
              marginTop: '10px',
            }}>
              {legendModels.map(m => (
                <div key={m.model} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <div style={{ width: '12px', height: '12px', background: modelColor(m.model), borderRadius: '2px' }} />
                  {familyMode && getModelIcon(m.iconModel, theme)}
                  <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-secondary)' }}>{familyMode ? t(m.label) : m.label}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
