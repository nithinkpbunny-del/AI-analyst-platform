import { BrowserRouter } from 'react-router'
import { AppRoutes } from './app/AppRoutes'
import { Providers } from './app/Providers'

export default function App() {
  return (
    <Providers>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </Providers>
  )
}
