import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

const App = lazy(() => window.location.pathname === '/cull' || window.location.pathname === '/cull/'
  ? import('./culling/CullingApp.tsx')
  : import('./App.tsx'))

export default App

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Suspense fallback={<p style={{ padding: 24 }}>正在打开 BirdShot…</p>}>
      <App />
    </Suspense>
  </StrictMode>,
)
