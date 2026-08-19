import { Navigate, Route, Routes } from 'react-router-dom'

import Layout from '@/components/Layout'
import { Loading } from '@/components/ui'
import { useAuth } from '@/hooks/useAuth'
import Dashboard from '@/pages/Dashboard'
import InvestigationDetail from '@/pages/InvestigationDetail'
import Investigations from '@/pages/Investigations'
import Login from '@/pages/Login'
import { HistoryPage, SettingsPage, SourcesPage } from '@/pages/Misc'
import PersonDetail from '@/pages/PersonDetail'
import Plugins from '@/pages/Plugins'
import SearchPage from '@/pages/SearchPage'

export default function App() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loading label="Checking session..." />
      </div>
    )
  }

  if (!user) {
    return (
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    )
  }

  return (
    <Routes>
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/investigations" element={<Investigations />} />
        <Route path="/investigations/:id" element={<InvestigationDetail />} />
        <Route path="/persons/:id" element={<PersonDetail />} />
        <Route path="/search/:kind" element={<SearchPage />} />
        <Route path="/plugins" element={<Plugins />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
