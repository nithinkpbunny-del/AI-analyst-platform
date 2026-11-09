import { useEffect, useState } from 'react'

/**
 * Read `#token=…` from an email link once, then remove it from the address bar so it is not kept in history
 * or shown over someone's shoulder.
 */
export function useLinkToken(): string | null {
  const [token] = useState(() => {
    const match = /(?:^#|&)token=([A-Za-z0-9_-]+)/.exec(window.location.hash)
    return match?.[1] ?? null
  })
  useEffect(() => {
    if (window.location.hash) {
      window.history.replaceState(
        window.history.state,
        '',
        window.location.pathname + window.location.search,
      )
    }
  }, [])
  return token
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export function looksLikeEmail(value: string): boolean {
  return EMAIL_PATTERN.test(value.trim())
}
