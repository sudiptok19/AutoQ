import { StrictMode, lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { AuthProvider, useAuth } from './auth/auth'
import { AppShell } from './layout/AppShell'
import { Spinner } from './components/ui'
import Login from './pages/Login'

const Overview = lazy(() => import('./pages/Overview'))
const Lots = lazy(() => import('./pages/Lots'))
const Inspect = lazy(() => import('./pages/Inspect'))
const Containment = lazy(() => import('./pages/Containment'))
const Packing = lazy(() => import('./pages/Packing'))
const Dispatch = lazy(() => import('./pages/Dispatch'))
const Trace = lazy(() => import('./pages/Trace'))
const Spc = lazy(() => import('./pages/Spc'))
const Audit = lazy(() => import('./pages/Audit'))
const Recalls = lazy(() => import('./pages/Recalls'))
const Reports = lazy(() => import('./pages/Reports'))
const UnitPage = lazy(() => import('./pages/UnitPage'))
const Scanner = lazy(() => import('./pages/Scanner'))

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 3000, retry: (n, e: any) => n < 1 && !(e?.status >= 400 && e?.status < 500), refetchOnWindowFocus: false } } })

function Gate() {
  const { user, loading } = useAuth()
  if (loading) return <Spinner label="Restoring session…" />
  if (!user) return <Login />
  return (
    <Suspense fallback={<Spinner />}>
      <Routes>
        <Route path="/scanner" element={<Scanner />} />
        <Route element={<AppShell />}>
          <Route index element={<Overview />} />
          <Route path="lots" element={<Lots />} />
          <Route path="lots/:id" element={<Lots />} />
          <Route path="units/:id" element={<UnitPage />} />
          <Route path="inspect" element={<Inspect />} />
          <Route path="containment" element={<Containment />} />
          <Route path="packing" element={<Packing />} />
          <Route path="dispatch" element={<Dispatch />} />
          <Route path="trace" element={<Trace />} />
          <Route path="spc" element={<Spc />} />
          <Route path="recalls" element={<Recalls />} />
          <Route path="recalls/:id" element={<Recalls />} />
          <Route path="reports" element={<Reports />} />
          <Route path="reports/:id" element={<Reports />} />
          <Route path="audit" element={<Audit />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Route>
      </Routes>
    </Suspense>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <AuthProvider><BrowserRouter><Gate /></BrowserRouter></AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
