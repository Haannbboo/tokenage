import { useCallback, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import './App.css'
import { installTooltips } from './tooltip'
import { AppProvider, useApp } from './contexts/AppContext'
import { AppShell } from './components/Sidebar'
import { LoginGate } from './components/LoginGate'
import { DashboardPage } from './pages/DashboardPage'
import { ProjectsPage } from './pages/ProjectsPage'
import { SessionsPage } from './pages/SessionsPage'
import { LogsPage } from './pages/LogsPage'
import { SettingsPage } from './pages/SettingsPage'

function AppLayout() {
  useEffect(() => installTooltips(), [])
  const navigate = useNavigate()

  const handleNavigateToLogs = useCallback((filters?: { sessionFilter?: string }) => {
    navigate(filters?.sessionFilter ? `/logs?${new URLSearchParams({ session: filters.sessionFilter })}` : '/logs')
  }, [navigate])

  return (
    <AppShell>
      <Routes>
        <Route path="/dashboard" element={<DashboardPage onNavigateToLogs={handleNavigateToLogs} />} />
        <Route path="/sessions" element={<SessionsPage onNavigateToLogs={handleNavigateToLogs} />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/logs" element={<LogsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AppShell>
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
