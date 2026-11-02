import { t } from '../i18n/index.ts'
import { useApp } from '../contexts/AppContext'
import { UserAvatar } from './UserAvatar'

type View = 'dashboard' | 'logs' | 'settings'

const ASCII_ART = `████████  ██████  ██   ██ ███████ ███    ██  █████   ██████  ███████
   ██    ██    ██ ██  ██  ██      ████   ██ ██   ██ ██       ██
   ██    ██    ██ █████   █████   ██ ██  ██ ███████ ██   ███ █████
   ██    ██    ██ ██  ██  ██      ██  ██ ██ ██   ██ ██    ██ ██
   ██     ██████  ██   ██ ███████ ██   ████ ██   ██  ██████  ███████`

export function Navbar({ currentView, onNavigate }: { currentView: View; onNavigate: (v: View) => void }) {
  const { theme, toggleThemeHandler, lang, setLang, auth } = useApp()

  return (
    <header className="top-navbar">
      <pre className="navbar-brand-art" aria-hidden="true">{ASCII_ART}</pre>
      <nav className="navbar-nav">
        <button className={`nav-item ${currentView === 'dashboard' ? 'active' : ''}`} onClick={() => onNavigate('dashboard')}>
          📊 {t('Dashboard')}
        </button>
        <button className={`nav-item ${currentView === 'logs' ? 'active' : ''}`} onClick={() => onNavigate('logs')}>
          📜 {t('Request Logs')}
        </button>
        <button className={`nav-item nav-item-settings ${currentView === 'settings' ? 'active' : ''}`} onClick={() => onNavigate('settings')}>
          {auth.provider === 'google' && auth.user ? (
            <>
              <UserAvatar user={auth.user} />
              <span className="user-email" title={auth.user.email}>
                {auth.user.name || auth.user.email}
              </span>
            </>
          ) : (
            <>⚙️ {t('Settings')}</>
          )}
        </button>
      </nav>
      <div className="navbar-actions">
        <button
          className="nav-item"
          style={{ fontSize: '18px' }}
          onClick={toggleThemeHandler}
          title={theme === 'dark' ? t('Switch to light mode') : t('Switch to dark mode')}
        >
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
        <button
          className="nav-item"
          style={{ fontSize: '13px', fontWeight: 700, minWidth: '36px', textAlign: 'center' }}
          onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}
          title={lang === 'zh' ? '切换到中文' : 'Switch to English'}
        >
          {lang === 'zh' ? '中' : 'EN'}
        </button>
      </div>
    </header>
  )
}
