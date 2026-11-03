import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext.tsx'

const AUTH_ERROR_MESSAGES: Record<string, string> = {
  invalid_state: 'This sign-in link was invalid or expired. Please try again.',
  email_unverified: 'Your Google account email is not verified.',
  not_allowlisted: 'Your Google account is not on this server\'s allowlist.',
  oauth_failed: 'Google sign-in failed. Please try again.',
}

export function LoginGate() {
  const location = useLocation()
  const { auth } = useApp()
  const params = new URLSearchParams(location.search)
  const authError = params.get('auth_error')
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    if (authError) {
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [authError])

  const errorMessage = authError && !dismissed ? AUTH_ERROR_MESSAGES[authError] : null

  return (
    <div className="login-gate">
      <div className="login-gate-card">
        <div className="login-gate-title">
          <span>{t('Sign in required')}</span>
        </div>
        <div className="login-gate-subtitle">
          {auth.provider === 'google'
            ? t('Sign in with your Google account to continue using tokenage.')
            : t('This server uses local sign-in. On the server machine, run `tokenage server login-link` and open the link it prints in this browser.')}
        </div>
        {errorMessage && (
          <div className="login-gate-error">
            <span>{t(errorMessage)}</span>
            <button type="button" className="login-gate-dismiss" onClick={() => setDismissed(true)}>✕</button>
          </div>
        )}
        {auth.provider === 'google' && (
          <a className="btn-primary login-gate-button" href="/auth/google/login">
            <span style={{ fontWeight: 700 }}>G</span>
            {t('Sign in with Google')}
          </a>
        )}
      </div>
    </div>
  )
}
