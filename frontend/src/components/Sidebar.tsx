import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import { useDismiss } from '../hooks/useDismiss'
import { useDevices } from '../hooks/useDevices'
import { useVersion } from '../hooks/useVersion'
import { Icon, type IconName } from './Icon'
import { UserAvatar } from './UserAvatar'

const ASCII_ART = `████████  ██████  ██   ██ ███████ ███    ██  █████   ██████  ███████
   ██    ██    ██ ██  ██  ██      ████   ██ ██   ██ ██       ██
   ██    ██    ██ █████   █████   ██ ██  ██ ███████ ██   ███ █████
   ██    ██    ██ ██  ██  ██      ██  ██ ██ ██   ██ ██    ██ ██
   ██     ██████  ██   ██ ███████ ██   ████ ██   ██  ██████  ███████`

const WORKSPACE: { to: string; label: string; icon: IconName }[] = [
  { to: '/dashboard', label: 'Dashboard', icon: 'dashboard' },
  { to: '/sessions', label: 'Sessions', icon: 'sessions' },
  { to: '/logs', label: 'Request Logs', icon: 'logs' },
]
const SETTINGS = { to: '/settings', label: 'Settings', icon: 'settings' as const }

const PHONE_QUERY = '(max-width: 639px)'
const TABLET_QUERY = '(max-width: 1023px)'
const COLLAPSED_KEY = 'tokenage-sidebar'

function useMediaQuery(query: string) {
  const subscribe = useCallback((notify: () => void) => {
    if (typeof window.matchMedia !== 'function') return () => {}
    const list = window.matchMedia(query)
    list.addEventListener('change', notify)
    return () => list.removeEventListener('change', notify)
  }, [query])
  return useSyncExternalStore(subscribe, () => typeof window.matchMedia === 'function' && window.matchMedia(query).matches)
}

function readCollapsed() {
  try { return localStorage.getItem(COLLAPSED_KEY) === 'collapsed' } catch { return false }
}

function isTyping(target: EventTarget | null) {
  return target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(target.tagName))
}

function pageTitle(pathname: string) {
  return [...WORKSPACE, SETTINGS].find(item => pathname.startsWith(item.to))?.label ?? 'Dashboard'
}

function ThemeLangButtons() {
  const { theme, toggleThemeHandler, lang, setLang } = useApp()
  const themeLabel = theme === 'dark' ? t('Switch to light mode') : t('Switch to dark mode')
  const langLabel = lang === 'zh' ? 'Switch to English' : '切换到中文'
  return <>
    <button type="button" className="icon-btn" onClick={toggleThemeHandler} aria-label={themeLabel} data-tip={themeLabel}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
    </button>
    <button type="button" className="icon-btn" onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')} aria-label={langLabel} data-tip={langLabel}>
      {lang === 'zh' ? '中' : 'EN'}
    </button>
  </>
}

function AccountMenu({ onNavigate }: { onNavigate: () => void }) {
  const { auth, signOut } = useApp()
  // Local-auth browsers signed in by a login link have a current web session to
  // end. If the list fails to load, offer Sign out rather than hide it.
  const { webSessions, error: devicesError } = useDevices(!!auth.user && auth.provider !== 'google')
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const close = useCallback((event?: Event) => {
    setOpen(false)
    if (event instanceof KeyboardEvent) triggerRef.current?.focus()
  }, [])
  useDismiss(ref, open, close)
  const user = auth.user
  if (!user) return null
  const name = user.name || user.email
  return (
    <div className="account" ref={ref}>
      <button
        ref={triggerRef}
        type="button"
        className="account-trigger"
        aria-expanded={open}
        aria-controls="account-menu"
        aria-label={`${t('Account')}: ${name}`}
        data-tip={name}
        onClick={() => setOpen(o => !o)}
      >
        <UserAvatar user={user} />
        <span className="account-rest">
          <span className="account-name">{name}</span>
          <Icon name="chevron" className="account-chevron" />
        </span>
      </button>
      {open && (
        <div id="account-menu" className="menu account-menu">
          <div className="menu-meta"><b>{name}</b>{user.email !== name && <span>{user.email}</span>}</div>
          <Link className="menu-item" to="/settings" onClick={() => { close(); onNavigate() }}><Icon name="settings" />{t('Settings')}</Link>
          {(auth.provider === 'google' || devicesError || webSessions?.some(session => session.current)) && (
            <button type="button" className="menu-item" onClick={() => void signOut()}><Icon name="signOut" />{t('Sign out')}</button>
          )}
        </div>
      )}
    </div>
  )
}

function SidebarLink({ to, label, icon, onNavigate }: { to: string; label: string; icon: IconName; onNavigate: () => void }) {
  return (
    <li>
      <NavLink to={to} onClick={onNavigate} className="sidebar-link" aria-label={t(label)} data-tip={t(label)}>
        <Icon name={icon} /><span className="sidebar-label">{t(label)}</span>
      </NavLink>
    </li>
  )
}

/** The app frame: left sidebar (icon rail when collapsed, a drawer on phones) around the page. */
export function AppShell({ children }: { children: ReactNode }) {
  const phone = useMediaQuery(PHONE_QUERY)
  const tablet = useMediaQuery(TABLET_QUERY) && !phone
  const location = useLocation()
  const version = useVersion()
  const sidebarRef = useRef<HTMLElement>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const [pinnedCollapsed, setPinnedCollapsed] = useState(readCollapsed)
  // Tablets show the icon rail; expanding it overlays the page until dismissed.
  const [tabletOpen, setTabletOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  if (!phone && drawerOpen) setDrawerOpen(false)
  if (!tablet && tabletOpen) setTabletOpen(false)
  const collapsed = phone ? false : tablet ? !tabletOpen : pinnedCollapsed

  const toggleCollapsed = useCallback(() => {
    if (tablet) { setTabletOpen(o => !o); return }
    setPinnedCollapsed(c => {
      try { localStorage.setItem(COLLAPSED_KEY, c ? 'expanded' : 'collapsed') } catch { /* private mode */ }
      return !c
    })
  }, [tablet])

  const closeDrawer = useCallback(() => {
    setDrawerOpen(false)
    menuButtonRef.current?.focus()
  }, [])
  // The drawer goes inert once a link navigates, so hand focus back to the menu button.
  const closeNavigation = () => { if (drawerOpen) closeDrawer(); setTabletOpen(false) }
  const closeOverlay = useCallback(() => setTabletOpen(false), [])
  useDismiss(sidebarRef, phone && drawerOpen, closeDrawer)
  useDismiss(sidebarRef, tablet && tabletOpen, closeOverlay)

  // A navigation closes the drawer and the tablet overlay (reset during render, not on mount).
  const [shownPath, setShownPath] = useState(location.pathname)
  if (shownPath !== location.pathname) {
    setShownPath(location.pathname)
    setDrawerOpen(false)
    setTabletOpen(false)
  }

  useEffect(() => {
    if (phone) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '[' && !e.metaKey && !e.ctrlKey && !e.altKey && !isTyping(e.target)) toggleCollapsed()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [phone, toggleCollapsed])

  const modal = phone && drawerOpen
  useEffect(() => {
    if (!modal) return
    sidebarRef.current?.querySelector<HTMLElement>('.sidebar-link')?.focus()
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = overflow }
  }, [modal])

  const trapFocus = (e: React.KeyboardEvent) => {
    if (!modal || e.key !== 'Tab') return
    const items = Array.from(sidebarRef.current?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])') ?? [])
    const first = items[0], last = items[items.length - 1]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
  }

  const appClass = ['app', phone && 'is-phone', tablet && 'is-tablet', collapsed && 'is-collapsed', modal && 'drawer-open'].filter(Boolean).join(' ')
  return (
    <div className={appClass}>
      <a className="skip-link" href="#content">{t('Skip to content')}</a>
      {modal && <div className="scrim" aria-hidden="true" />}
      <aside
        id="sidebar"
        ref={sidebarRef}
        className="sidebar"
        aria-label={t('Sidebar')}
        role={modal ? 'dialog' : undefined}
        aria-modal={modal || undefined}
        inert={phone && !drawerOpen}
        onKeyDown={trapFocus}
      >
        <div className="sidebar-brand">
          <Link to="/dashboard" onClick={closeNavigation} className="sidebar-logo" aria-label="tokenage">
            <pre aria-hidden="true">{ASCII_ART}</pre>
          </Link>
          {!phone && (
            <button
              type="button"
              className="icon-btn sidebar-collapse"
              aria-expanded={!collapsed}
              aria-controls="sidebar"
              aria-label={collapsed ? t('Expand sidebar') : t('Collapse sidebar')}
              data-tip={collapsed ? t('Expand sidebar') : t('Collapse sidebar')}
              onClick={toggleCollapsed}
            >
              <Icon name="panel" />
            </button>
          )}
        </div>
        <AccountMenu onNavigate={closeNavigation} />
        <nav className="sidebar-nav" aria-label={t('Main')}>
          <ul className="sidebar-list">
            {WORKSPACE.map(item => <SidebarLink key={item.to} {...item} onNavigate={closeNavigation} />)}
            <li>
              <span className="sidebar-link is-disabled" role="link" aria-disabled="true" aria-label={`${t('Projects')} · ${t('Soon')}`} data-tip={`${t('Projects')} · ${t('Soon')}`}>
                <Icon name="projects" /><span className="sidebar-label has-pill">{t('Projects')}<span className="pill">{t('Soon')}</span></span>
              </span>
            </li>
          </ul>
          <ul className="sidebar-list sidebar-foot">
            <SidebarLink {...SETTINGS} onNavigate={closeNavigation} />
          </ul>
        </nav>
        <div className="sidebar-utils">
          {!phone && <ThemeLangButtons />}
          {version && <span className="sidebar-version">v{version.version}</span>}
        </div>
      </aside>
      <div className="main">
        {phone && (
          <header className="topbar">
            <button
              ref={menuButtonRef}
              type="button"
              className="icon-btn"
              aria-label={t('Open menu')}
              aria-expanded={drawerOpen}
              aria-controls="sidebar"
              onClick={() => setDrawerOpen(true)}
            >
              <Icon name="menu" />
            </button>
            <span className="topbar-title">{t(pageTitle(location.pathname))}</span>
            <ThemeLangButtons />
          </header>
        )}
        <main id="content" className="content-body" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  )
}
