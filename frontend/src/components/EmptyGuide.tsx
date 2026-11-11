import { CopyButton } from './CopyButton'
import { useVersion } from '../hooks/useVersion'
import { t } from '../i18n'
import { formatCost, formatLatency, formatNumber, getAgentDisplayName, value } from '../utils'
import type { UsageRow } from '../types'

const GITHUB_INSTALL = 'curl -fsSL https://raw.githubusercontent.com/Haannbboo/tokenage/main/install.sh | sh'
const DOCKER_README = 'https://github.com/Haannbboo/tokenage/blob/main/docker/README.md'
const AGENTS = ['claude', 'codex', 'opencode', 'kilo']

function parseServerUrl(serverUrl: string) {
  try {
    return new URL(serverUrl)
  } catch {
    return null
  }
}

/** The empty Overview: install the client, use an agent, see the first request. */
export function EmptyGuide({ verificationResult, onViewLogs, onDone }: {
  verificationResult: UsageRow | null
  onViewLogs: () => void
  onDone: () => void
}) {
  const version = useVersion()
  // Invalid API origins are withheld by /version. The browser origin still
  // tells an HTTP self-host what must be fixed before installing a client.
  const serverUrl = version?.server_url || window.location.origin
  const origin = parseServerUrl(serverUrl)
  const needsHttps = origin?.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname)
  const install = origin && version?.server_url && version.installer_available
    ? `curl -fsSL ${version.server_url}/install.sh | sh`
    : GITHUB_INSTALL

  return (
    <div className="empty-guide">
      <div className="empty-guide-hero">
        <h2>{t('No usage tracked yet')}</h2>
        <p>{t('Install the tokenage client on the computer where you run your coding agents. It signs you in and sets up the agents it finds.')}</p>
      </div>

      <section className="empty-guide-step">
        <div className="empty-guide-label">{t('1 · Install the client')}</div>
        {version !== null && !needsHttps && <div className="empty-guide-cmd">
          <code>{install}</code>
          <CopyButton text={install} className="btn-ghost empty-guide-copy" idleLabel={t('Copy')} copiedLabel={`✓ ${t('Copied!')}`} />
        </div>}
        {version !== null && needsHttps && (
          <p className="empty-guide-hint">
            {t('This server needs HTTPS before clients can connect')} · <a href={DOCKER_README} target="_blank" rel="noreferrer">{t('Self-hosting with Docker')}</a>
          </p>
        )}
        {version !== null && origin && !version?.installer_available && !needsHttps && (
          <p className="empty-guide-hint">
            {t('This server cannot serve the installer. When it asks for the server URL, enter:')} <code>{serverUrl}</code>
          </p>
        )}
        {!needsHttps && <p className="empty-guide-hint">
          {t('It asks which agents to track. Already installed? Run')} <code>tokenage update</code>.
        </p>}
      </section>

      <section className="empty-guide-step">
        <div className="empty-guide-label">{t('2 · Use an agent as usual')}</div>
        <div className="empty-guide-agents">
          {AGENTS.map(command => (
            <span key={command} className="empty-guide-agent">{getAgentDisplayName(command)} <code>{command}</code></span>
          ))}
        </div>
        <p className="empty-guide-hint">{t('No wrapper needed. Every request is tracked in the background.')}</p>
      </section>

      <section className="empty-guide-step">
        <div className="empty-guide-label">{t('3 · Your first request appears here')}</div>
        <div role="status" aria-live="polite">
          {verificationResult ? (
            <>
              <div className="empty-guide-ok">{t('Tracking works. Your first request is recorded.')}</div>
              <div className="empty-guide-kv">
                <div><span>{t('Source:')}</span> {verificationResult.client_source ? getAgentDisplayName(verificationResult.client_source) : '—'}</div>
                <div><span>{t('Model:')}</span> {verificationResult.model || '—'}</div>
                <div><span>{t('Tokens:')}</span> {t('In:')} {formatNumber(verificationResult.prompt_tokens)} / {t('Out:')} {formatNumber(verificationResult.completion_tokens)}</div>
                <div><span>{t('Cost:')}</span> {formatCost(value(verificationResult.total_cost_usd))}</div>
                <div><span>{t('Latency:')}</span> {formatLatency(verificationResult.latency_ms)}</div>
              </div>
              <div className="empty-guide-buttons">
                <button className="btn-primary" onClick={onViewLogs}>{t('View request logs')}</button>
                <button className="btn-ghost" onClick={onDone}>{t('Go to dashboard')}</button>
              </div>
            </>
          ) : (
            <div className="empty-guide-wait">
              <span className="empty-guide-dot" aria-hidden="true" />
              {t('Waiting for your first request. This page checks automatically.')}
            </div>
          )}
        </div>
      </section>

      <p className="empty-guide-foot">
        {t('Running your own server?')} <a href={DOCKER_README} target="_blank" rel="noreferrer">{t('Self-hosting with Docker')}</a>
      </p>
    </div>
  )
}
