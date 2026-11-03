import type { UsageRow } from '../types'
import type { RequestLogColumnId } from '../hooks/useRequestLogColumns'
import { t } from '../i18n'
import { formatCost, formatLatency, formatNumber, formatRate, formatSpeed, formatTime, value, getProviderIcon, getModelIcon, getSourceIcon, shortSessionId, ToolBadge, formatModelName, formatProviderName } from '../utils'

type RequestCellOptions = { tz?: string; colWidths: { model: number; device: number }; hoveredSession: string | null; setSessionFilter: (id: string) => void; recalculatingId: string | null; handleRecalculateCost: (id: string) => Promise<void>; stopToolPropagation?: boolean }

export function renderRequestCell(row: UsageRow, columnId: RequestLogColumnId | 'tokens' | 'latency', { tz, colWidths, hoveredSession, setSessionFilter, recalculatingId, handleRecalculateCost, stopToolPropagation }: RequestCellOptions) {
    switch (columnId) {
      case 'tokens':
        return <td>{formatNumber(row.total_tokens ?? ((row.prompt_tokens ?? 0) + (row.completion_tokens ?? 0)))}</td>
      case 'latency':
        return <td>{formatLatency(row.latency_ms)}</td>
      case 'time':
        return <td style={{ color: 'var(--text-secondary)' }}>{formatTime(row.ts, tz)}</td>
      case 'model':
        return (
          <td style={{ padding: '8px' }}>
            <div className="entity-badge mono" style={{ maxWidth: colWidths.model - 10, cursor: 'pointer' }} title={row.model}>
              {getModelIcon(row.model)}
              <span style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap'
              }}>
                {formatModelName(row.model)}
              </span>
            </div>
          </td>
        )
      case 'provider':
        return (
          <td style={{ padding: '8px' }}>
            <div className="entity-badge">
              {getProviderIcon(row.provider)}
              {formatProviderName(row.provider)}
            </div>
          </td>
        )
      case 'source':
        return (
          <td style={{ padding: '8px', paddingLeft: '16px' }}>
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              maxWidth: '120px',
            }}>
              {getSourceIcon(row.client_source ?? '') ?? (
                <span className="entity-badge">
                  {row.client_source || '—'}
                </span>
              )}
            </div>
          </td>
        )
      case 'device':
        return (
          <td style={{ color: row.device_name ? 'var(--text-secondary)' : 'var(--text-muted)', maxWidth: colWidths.device, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.device_name ?? undefined}>
            {row.device_name ?? t('Unknown')}
          </td>
        )
      case 'session':
        return (
          <td className="request-log-session-cell">
            {row.session_id ? (() => {
              const sessionId = row.session_id
              return (
                <div className="request-log-session-actions">
                  <button
                    type="button"
                    className={`request-log-session-filter${hoveredSession === sessionId ? ' session-hover-active' : ''}`}
                    title={sessionId}
                    aria-label={`${t('Filter logs by session')}: ${sessionId}`}
                    onClick={(e) => { e.stopPropagation(); setSessionFilter(sessionId) }}
                  >
                    {shortSessionId(sessionId)}
                  </button>
                </div>
              )
            })() : (
              <span className="request-log-session-empty">&mdash;</span>
            )}
          </td>
        )
      case 'input':
        return (
          <td style={{ verticalAlign: 'top' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>
                {formatNumber(row.prompt_tokens)}
                <span style={{ fontSize: '10px', fontWeight: 400, marginLeft: '4px', color: 'var(--text-secondary)' }}>{t('tokens')}</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '2px' }}>
                {value(row.cached_tokens) > 0 && (
                  <div style={{ fontSize: '10px', color: 'var(--color-green)', fontWeight: 500 }}>
                    {/* cache_creation_tokens is disjoint from prompt_tokens (Anthropic semantics),
                        so it's added to the denominator for a true hit rate. */}
                    {t('Cache read')} {formatNumber(row.cached_tokens)} ({Math.round((value(row.cached_tokens) / ((value(row.prompt_tokens) + value(row.cache_creation_tokens)) || 1)) * 100)}%)
                  </div>
                )}
                {value(row.cache_creation_tokens) > 0 && (
                  <div style={{ fontSize: '10px', color: 'var(--color-yellow)', fontWeight: 500 }}>
                    {t('Cache write')} {formatNumber(row.cache_creation_tokens)}
                  </div>
                )}
              </div>
            </div>
            <div className="has-tooltip" style={{ width: '100%', borderBottom: 'none' }}>
              {(() => {
                const promptUncached = Math.max(0, value(row.prompt_tokens) - value(row.cached_tokens));
                const cacheCreation = value(row.cache_creation_tokens);
                // total_tokens includes cache_creation_tokens for Anthropic responses,
                // so it's rendered as its own segment or the bar won't fill correctly.
                const total = value(row.total_tokens) || 1;
                return (
                  <>
                    <div style={{
                      width: '100%',
                      height: '3px',
                      background: 'var(--progress-bg)',
                      borderRadius: '2px',
                      marginTop: '4px',
                      overflow: 'hidden',
                      border: '1px solid var(--border-color)',
                      display: 'flex'
                    }}>
                      <div style={{ height: '100%', background: 'var(--color-green)', width: `${(value(row.cached_tokens) / total) * 100}%` }} />
                      <div style={{ height: '100%', background: 'var(--chart-2)', width: `${(promptUncached / total) * 100}%`, opacity: 0.7 }} />
                      {cacheCreation > 0 && (
                        <div style={{ height: '100%', background: 'var(--color-yellow)', width: `${(cacheCreation / total) * 100}%` }} />
                      )}
                      <div style={{ height: '100%', background: 'var(--color-blue)', width: `${(value(row.completion_tokens) / total) * 100}%` }} />
                    </div>
                    <div className="tooltip-text">
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
                          <span style={{ color: 'var(--color-green)' }}>&bull; {t('Cached')}:</span>
                          <span style={{ fontWeight: 600 }}>{formatNumber(row.cached_tokens)}</span>
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
                          <span style={{ color: 'var(--chart-2)' }}>&bull; {t('Input')}:</span>
                          <span style={{ fontWeight: 600 }}>{formatNumber(promptUncached)}</span>
                        </div>
                        {cacheCreation > 0 && (
                          <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
                            <span style={{ color: 'var(--color-yellow)' }}>&bull; {t('Cache write')}:</span>
                            <span style={{ fontWeight: 600 }}>{formatNumber(cacheCreation)}</span>
                          </div>
                        )}
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
                          <span style={{ color: 'var(--color-blue)' }}>&bull; {t('Output')}:</span>
                          <span style={{ fontWeight: 600 }}>{formatNumber(row.completion_tokens)}</span>
                        </div>
                      </div>
                    </div>
                  </>
                );
              })()}
            </div>
          </td>
        )
      case 'output':
        return (
          <td style={{ verticalAlign: 'top' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div>{formatNumber(row.completion_tokens)}</div>
              {value(row.reasoning_tokens) > 0 && (
                <div style={{ fontSize: '10px', color: 'var(--text-secondary)', fontWeight: 500 }}>
                  {t('Reasoning')} {formatNumber(row.reasoning_tokens)} ({Math.min(Math.round((value(row.reasoning_tokens) / (value(row.completion_tokens) || 1)) * 100), 100)}%)
                </div>
              )}
            </div>
            {value(row.reasoning_tokens) > 0 && (
              <div
                style={{
                  width: '100%',
                  height: '3px',
                  background: 'var(--progress-bg)',
                  borderRadius: '2px',
                  marginTop: '4px',
                  overflow: 'hidden',
                  border: '1px solid var(--border-color)',
                  display: 'flex'
                }}
              >
                {value(row.reasoning_tokens) > 0 && (
                  <div
                    title={`Reasoning: ${formatNumber(row.reasoning_tokens)} tokens`}
                    style={{
                      width: `${Math.min((value(row.reasoning_tokens) / (value(row.completion_tokens) || 1)) * 100, 100)}%`,
                      height: '100%',
                      background: 'var(--chart-2)'
                    }}
                  />
                )}
              </div>
            )}
          </td>
        )
      case 'cost':
        return (
          <td style={{ padding: '8px' }}>
            {(() => {
              const total = value(row.total_cost_usd);
              const isRecalculating = recalculatingId === row.id;
              const costValueClass = isRecalculating ? 'cost-recalculating' : '';
              const handleCostClick = (e: React.MouseEvent) => {
                e.stopPropagation();
                void handleRecalculateCost(row.id);
              };

              const prompt = value(row.prompt_tokens);
              const cached = value(row.cached_tokens);
              const cacheCreation = value(row.cache_creation_tokens);
              const outputCost = value(row.output_cost_usd);

              // The backend splits input cost by component at record time using
              // the selected pricing tier; use those values directly instead of
              // re-deriving them from input_cost_usd by token ratios.
              const actualInputCost = value(row.normal_input_cost_usd);
              const cacheCost = value(row.cache_read_cost_usd);
              const cacheWriteCost = value(row.cache_write_cost_usd);

              const uncachedTokens = Math.max(0, prompt - cached);
              const completionTokens = value(row.completion_tokens);

              // Rates and provenance come from the backend's pricing snapshot so
              // they reflect exactly how this row was priced.
              const pricing = row.pricing;

              return (
                <div className="has-tooltip" style={{ borderBottom: 'none' }}>
                  <button
                    type="button"
                    className={`cost-recalc-btn ${costValueClass}`}
                    style={{ color: 'var(--text-primary)', fontWeight: 500 }}
                    onClick={handleCostClick}
                    aria-label={t('Click to recalculate')}
                  >
                    {formatCost(total)}
                  </button>
                  {row.cost_estimated && (
                    <span
                      title={t('Estimated: priced from current rates, not a stored snapshot')}
                      style={{
                        marginLeft: '4px',
                        fontSize: '10px',
                        color: 'var(--badge-warn-text)',
                      }}
                    >
                      est.
                    </span>
                  )}
                  <div className="tooltip-text cost-tooltip">
                    <div className="cost-tooltip-rows">
                      <div className="cost-tooltip-row">
                        <span className="cost-tooltip-label">{t('Input:')}</span>
                        <div className="cost-tooltip-amount">
                          <div>{formatCost(actualInputCost)}</div>
                          {pricing && (
                            <div className="cost-tooltip-detail">{formatNumber(uncachedTokens)} tokens x {formatRate(pricing.input)}</div>
                          )}
                        </div>
                      </div>
                      <div className="cost-tooltip-row">
                        <span className="cost-tooltip-label">{t('Output:')}</span>
                        <div className="cost-tooltip-amount">
                          <div>{formatCost(outputCost)}</div>
                          {pricing && (
                            <div className="cost-tooltip-detail">{formatNumber(completionTokens)} tokens x {formatRate(pricing.output)}</div>
                          )}
                        </div>
                      </div>
                      {cacheCost > 0 && (
                        <div className="cost-tooltip-row">
                          <span className="cost-tooltip-label">{t('Cache:')}</span>
                          <div className="cost-tooltip-amount">
                            <div style={{ color: 'var(--color-green)' }}>{formatCost(cacheCost)}</div>
                            {pricing && (
                              <div className="cost-tooltip-detail">{formatNumber(cached)} tokens x {formatRate(pricing.cache_read)}</div>
                            )}
                          </div>
                        </div>
                      )}
                      {cacheWriteCost > 0 && (
                        <div className="cost-tooltip-row">
                          <span className="cost-tooltip-label">{t('Cache write:')}</span>
                          <div className="cost-tooltip-amount">
                            <div style={{ color: 'var(--color-yellow)' }}>{formatCost(cacheWriteCost)}</div>
                            {pricing && pricing.cache_write !== null && (
                              <div className="cost-tooltip-detail">{formatNumber(cacheCreation)} tokens x {formatRate(pricing.cache_write)}</div>
                            )}
                          </div>
                        </div>
                      )}
                      <div className="cost-tooltip-row cost-tooltip-total">
                        <span className="cost-tooltip-label">{t('Total:')}</span>
                        <span>{formatCost(total)}</span>
                      </div>
                      {pricing && (
                        <div className="cost-tooltip-meta">
                          <div>
                            {t('Source:')} {pricing.source ?? t('unknown')}
                            {pricing.estimated && ` (${t('estimated')})`}
                          </div>
                          {pricing.tier && (
                            <div>
                              {t('Tier:')} {formatNumber(pricing.tier.min_tokens)}
                              {pricing.tier.max_tokens === null ? '+' : `–${formatNumber(pricing.tier.max_tokens)}`} {t('tokens')}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                    <div className="cost-tooltip-footer">{t('Click to recalculate')}</div>
                  </div>
                </div>
              );
            })()}
          </td>
        )
      case 'speed':
        return (
          <td style={{ padding: '8px', fontWeight: 600 }}>
            {(() => {
              const tokens = row.completion_tokens ?? row.total_tokens
              const latency = value(row.latency_ms)
              const speed = formatSpeed(tokens, latency)
              return speed ? (
                <span title={`${formatNumber(tokens)} tokens / ${formatLatency(latency)}`}>
                  <span>{speed.split(' ')[0]}</span>
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 400, marginLeft: '3px' }}>{speed.split(' ')[1]}</span>
                </span>
              ) : <span style={{ color: 'var(--text-muted)', fontSize: '12px', fontWeight: 400 }}>—</span>
            })()}
          </td>
        )
      case 'status':
        return (
          <td>
            <span className={`badge ${value(row.status) >= 400 ? 'badge-error' : 'badge-success'}`}>
              {row.status ?? '200'}
            </span>
          </td>
        )
      case 'tool':
        return (
          <td>
            {row.tool_names ? (
              <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap' }} onClick={stopToolPropagation ? e => e.stopPropagation() : undefined}>
                {(row.tool_names as string).split(',').filter(Boolean).map((name: string, i: number) => (
                  <ToolBadge key={i} name={name} />
                ))}
              </div>
            ) : null}
          </td>
        )
      default:
        return <td />
    }
  }
