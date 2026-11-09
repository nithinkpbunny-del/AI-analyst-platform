import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { AuthProvider, StaticAuthProvider } from '../lib/auth'
import type { SessionInfo } from '../lib/authApi'
import '../lib/i18n'
import { ThemeProvider } from '../lib/theme'

/**
 * App-wide providers. `session` replaces the real auth provider with a fixed signed-in session (Storybook and
 * component tests that render screens without an API).
 */
export function Providers({ children, session }: { children: ReactNode; session?: SessionInfo }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } },
      }),
  )
  const content = session ? (
    <StaticAuthProvider session={session}>{children}</StaticAuthProvider>
  ) : (
    <AuthProvider>{children}</AuthProvider>
  )
  return (
    <QueryClientProvider client={client}>
      <ThemeProvider>{content}</ThemeProvider>
    </QueryClientProvider>
  )
}
