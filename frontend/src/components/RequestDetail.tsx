import type { UsageRow } from '../types'
import { ClickToCopy } from './CopyButton'
import { RequestTimeline, type TimelineTool } from './RequestTimeline'
import { t } from '../i18n'
import { formatLatency, formatNumber, value, getSourceBadgeBg, getSourceBadgeText, getSourceIcon } from '../utils'

export function RequestDetail({ row, toolCalls, showToast }: { row: UsageRow; toolCalls: TimelineTool[] | null; showToast: (message: string) => void }) {
  return (
                        <div className="expanded-detail">
                          <div className="detail-group">
                            <span className="detail-label">{t('Request ID')}</span>
                            <span className="detail-value">
                              <ClickToCopy text={row.id} onCopy={showToast}>
                                {row.id}
                              </ClickToCopy>
                            </span>
                          </div>
                          {row.session_id && (
                            <div className="detail-group">
                              <span className="detail-label">{t('Session ID')}</span>
                              <span className="detail-value">
                                <ClickToCopy text={row.session_id} onCopy={showToast}>
                                  {row.session_id}
                                </ClickToCopy>
                              </span>
                            </div>
                          )}
                          <div className="detail-group">
                            <span className="detail-label">{t('Source')}</span>
                            <span className="detail-value" style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: '4px',
                              padding: '2px 8px',
                              borderRadius: '4px',
                              fontSize: '11px',
                              fontWeight: 600,
                              width: 'fit-content',
                              backgroundColor: getSourceBadgeBg(row.client_source ?? ''),
                              color: getSourceBadgeText(row.client_source ?? ''),
                            }}>
                              {getSourceIcon(row.client_source ?? '')}
                              {row.client_source || '—'}
                            </span>
                          </div>
                          <div className="detail-group">
                            <span className="detail-label">{t('Latency')}</span>
                            <span className="detail-value">{formatLatency(row.latency_ms)}</span>
                          </div>
                          <div className="detail-group">
                            <span className="detail-label">{t('Endpoint')}</span>
                            <span className="detail-value">{row.endpoint}</span>
                          </div>
                          <div className="detail-group">
                            <span className="detail-label">{t('Total Tokens')}</span>
                            <span className="detail-value">{formatNumber(row.total_tokens ?? (value(row.prompt_tokens) + value(row.completion_tokens)))}</span>
                          </div>
                          {value(row.tool_tokens) > 0 && (
                            <div className="detail-group">
                              <span className="detail-label">{t('Tool Tokens')}</span>
                              <span className="detail-value">{formatNumber(row.tool_tokens)}</span>
                            </div>
                          )}
                          {value(row.prompt_length) > 0 && (
                            <div className="detail-group">
                              <span className="detail-label">{t('Prompt Length')}</span>
                              <span className="detail-value">{formatNumber(row.prompt_length)} {t('chars')}</span>
                            </div>
                          )}
                          {value(row.ttft_ms) > 0 && (
                            <div className="detail-group">
                              <span className="detail-label">TTFT</span>
                              <span className="detail-value">{formatLatency(row.ttft_ms)}</span>
                            </div>
                          )}
                          <div className="detail-group">
                            <span className="detail-label">{t('Device')}</span>
                            <span className="detail-value">{row.device_name ?? t('Unknown')}</span>
                          </div>
                          {toolCalls && (
                            <RequestTimeline
                              latencyMs={row.latency_ms}
                              ttftMs={row.ttft_ms}
                              toolCalls={toolCalls}
                            />
                          )}
                        </div>
  )
}
