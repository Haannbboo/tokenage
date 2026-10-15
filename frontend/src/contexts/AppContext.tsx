import { createContext, useContext, useState, useCallback, useEffect } from 'react'
import type { ReactNode } from 'react'
import { toggleTheme, getTheme } from '../theme'
import { useLang } from '../i18n/index.ts'
import { getSavedTimezone, saveTimezone } from '../utils'
import type { ActiveFilter, AuthUser, DateRangeOption } from '../types.ts'

export type AuthState = {
  status: 'loading' | 'ready'
  // null until /auth/me answers (or when it can't be reached)
  provider: 'local' | 'google' | null
  user: AuthUser | null
}

type AppContextType = {
  theme: 'light' | 'dark'
  toggleThemeHandler: () => void

  lang: 'en' | 'zh'
  setLang: (l: 'en' | 'zh') => void

  auth: AuthState
  signOut: () => Promise<void>

  configStatus: 'idle' | 'saving' | 'saved' | 'error'
  setConfigStatus: (s: 'idle' | 'saving' | 'saved' | 'error') => void

  showToast: (message: string) => void

  error: string | null
  setError: (e: string | null) => void

  refreshTrigger: number
  requestUsageRefresh: () => void

  activeFilter: ActiveFilter
  setActiveFilter: (f: ActiveFilter) => void
  activeSource: string | null
  setActiveSource: (s: string | null) => void
  activeDevice: string | null
  setActiveDevice: (d: string | null) => void
  dateRange: DateRangeOption
  setDateRange: (d: DateRangeOption) => void
  customSince: string
  setCustomSince: (s: string) => void
  customUntil: string
  setCustomUntil: (s: string) => void

  timezone: string
  setTimezone: (tz: string) => void
}

const AppContext = createContext<AppContextType | null>(null)

export function isApiPath(pathname: string): boolean {
  return (
    pathname === '/auth' ||
    pathname.startsWith('/auth/') ||
    pathname === '/usage' ||
    pathname.startsWith('/usage/') ||
    pathname === '/sessions' ||
    pathname.startsWith('/sessions/') ||
    pathname === '/model-effectiveness' ||
    pathname === '/devices/status' ||
    pathname.startsWith('/config/') ||
    pathname === '/pricing' ||
    pathname.startsWith('/pricing/') ||
    pathname === '/evaluation-jobs' ||
    pathname.startsWith('/evaluation-jobs/') ||
    pathname === '/version'
  )
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<'light' | 'dark'>(getTheme)
  const { lang, setLang } = useLang()
  const [auth, setAuth] = useState<AuthState>({ status: 'loading', provider: null, user: null })
  const [configStatus, setConfigStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [refreshTrigger, setRefreshTrigger] = useState(0)

  const [activeFilter, setActiveFilter] = useState<ActiveFilter>(null)
  const [activeSource, setActiveSource] = useState<string | null>(null)
  const [activeDevice, setActiveDevice] = useState<string | null>(null)
  const [dateRange, setDateRange] = useState<DateRangeOption>('24h')
  const [customSince, setCustomSince] = useState('')
  const [customUntil, setCustomUntil] = useState('')
  const [timezone, setTimezoneState] = useState<string>(getSavedTimezone)
  const setTimezone = useCallback((tz: string) => { setTimezoneState(tz); saveTimezone(tz) }, [])

  const [toast, setToast] = useState<{ message: string; visible: boolean }>({ message: '', visible: false })
  const showToast = useCallback((message: string) => {
    setToast({ message, visible: true })
    setTimeout(() => setToast(prev => ({ ...prev, visible: false })), 2000)
  }, [])

  const requestUsageRefresh = useCallback(() => {
    setRefreshTrigger(trigger => trigger + 1)
  }, [])

  const toggleThemeHandler = useCallback(() => {
    setTheme(toggleTheme())
  }, [])

  const markLoggedOut = useCallback(() => {
    setAuth(current => {
      if (current.user === null && current.status === 'ready') return current
      return { status: 'ready', provider: current.provider, user: null }
    })
  }, [])

  const signOut = useCallback(async () => {
    try {
      await fetch('/auth/logout', { method: 'POST' })
    } catch {}
    window.location.reload()
  }, [])

  // Resolve the session on mount. Without a user (google: not signed in;
  // local: a browser that is not on the server machine) the app renders the
  // LoginGate instead of the dashboard.
  useEffect(() => {
    const controller = new AbortController()
    async function fetchAuth() {
      try {
        const response = await fetch('/auth/me', { signal: controller.signal })
        if (response.ok) {
          const data = await response.json()
          setAuth({
            status: 'ready',
            provider: data.provider === 'google' ? 'google' : 'local',
            user: data.user ?? null,
          })
          return
        }
        // Any failure (500, ...): fail closed rather than rendering the
        // dashboard with an unresolved auth state; "can't tell" is "logged out."
        setAuth({ status: 'ready', provider: null, user: null })
      } catch (err) {
        if (controller.signal.aborted) return
        console.error('Failed to resolve session:', err)
        setAuth({ status: 'ready', provider: null, user: null })
      }
    }
    void fetchAuth()
    return () => controller.abort()
  }, [])

  // Any 401 from the API means the session died (revoked cookie, expired
  // token); drop back to the login gate.
  useEffect(() => {
    const originalFetch = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const response = await originalFetch(input, init)
      if (response.status === 401) {
        const raw = typeof input === 'string'
          ? input
          : input instanceof Request
            ? input.url
            : input.href
        const url = new URL(raw, window.location.origin)
        if (isApiPath(url.pathname)) markLoggedOut()
      }
      return response
    }
    return () => { window.fetch = originalFetch }
  }, [markLoggedOut])

  return (
    <AppContext.Provider value={{
      theme, toggleThemeHandler,
      lang, setLang,
      auth, signOut,
      configStatus, setConfigStatus,
      showToast,
      error, setError,
      refreshTrigger, requestUsageRefresh,
      activeFilter, setActiveFilter, activeSource, setActiveSource,
      activeDevice, setActiveDevice,
      dateRange, setDateRange, customSince, setCustomSince, customUntil, setCustomUntil,
      timezone, setTimezone,
    }}>
      {children}
      <div className={`toast-container ${toast.visible ? 'visible' : ''}`}>
        {toast.message}
      </div>
    </AppContext.Provider>
  )
}

export function useApp() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used within AppProvider')
  return ctx
}
