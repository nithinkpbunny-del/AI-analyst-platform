import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from './app/AppRoutes'
import { Providers } from './app/Providers'
import type { SessionInfo } from './lib/authApi'
import { demoSession } from './lib/demoSession'

/**
 * Render the whole app at `path`. By default with a fixed signed-in Owner; pass `session: null` to use the
 * real auth provider (then mock `fetch` for /auth/*).
 */
export function renderApp(
  path = '/today',
  { session = demoSession }: { session?: SessionInfo | null } = {},
) {
  return render(
    <Providers session={session ?? undefined}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </Providers>,
  )
}

export function renderWithRouter(ui: ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}
