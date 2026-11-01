import { useCallback, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'
import './App.css'
import { installTooltips } from './tooltip'
import { AppProvider, useApp } from './contexts/AppContext'
import { Navbar } from './components/Navbar'
import { LoginGate } from './components/LoginGate'
import { DashboardPage } from './pages/DashboardPage'
import { LogsPage } from './pages/LogsPage'
import { SettingsPage } from './pages/SettingsPage'


function AppLayout() {
  useEffect(() => installTooltips(), [])
  const navigate = useNavigate()
  const location = useLocation()

  const currentView = location.pathname.startsWith('/logs')
    ? 'logs'
    : location.pathname.startsWith('/settings')
      ? 'settings'
      : 'dashboard'

  const handleNavigate = useCallback((view: 'dashboard' | 'logs' | 'settings') => {
    navigate(`/${view}`)
  }, [navigate])

  const handleNavigateToLogs = useCallback((filters?: { sessionFilter?: string }) => {
    if (filters?.sessionFilter) {
      sessionStorage.setItem('tokenage-logs-filters', JSON.stringify(filters))
    } else {
      sessionStorage.removeItem('tokenage-logs-filters')
    }
    navigate('/logs')
  }, [navigate])

  return (
    <div className="app">
      <main className="main">
        <Navbar currentView={currentView} onNavigate={handleNavigate} />
        <div className="content-body">
          <Routes>
            <Route path="/dashboard" element={<DashboardPage onNavigateToLogs={handleNavigateToLogs} />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route path="/pricing" element={<Navigate to="/settings?section=pricing" replace />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}

function Root() {
  const { auth } = useApp()
  if (auth.status === 'loading') {
    return (
      <div className="app">
        <main className="main">
          <div className="content-body" style={{ display: 'flex', justifyContent: 'center', paddingTop: '40vh' }}>
            <span style={{ color: 'var(--text-muted)', fontSize: '14px' }}>{'Loading...'}</span>
          </div>
        </main>
      </div>
    )
  }
  if (!auth.user) {
    return <LoginGate />
  }
  return <AppLayout />
}

function App() {
  return (
    <BrowserRouter>
      <AppProvider>
        <Root />
      </AppProvider>
    </BrowserRouter>
  )
}

export default App
