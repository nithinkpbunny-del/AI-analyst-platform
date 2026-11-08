import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { renderApp } from '../../test-utils'

function respond(routes: Record<string, unknown>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      const path = Object.keys(routes).find((p) => url.endsWith(p))
      if (!path) return Promise.reject(new TypeError('Failed to fetch'))
      return Promise.resolve({ json: () => Promise.resolve(routes[path]) })
    }),
  )
}

describe('System status on Settings', () => {
  it('shows the API version and each service check when connected', async () => {
    respond({
      '/health': { status: 'ok', service: 'analyst-api', version: '0.1.0', env: 'development' },
      '/health/ready': { status: 'ok', checks: { database: 'ok', redis: 'ok', storage: 'failed' } },
    })
    renderApp('/settings')
    expect(await screen.findByText(/API connected: version 0.1.0/)).toBeInTheDocument()
    const checks = screen.getByRole('list', { name: 'Service checks' })
    const rows = Array.from(checks.querySelectorAll('li')).map((li) => li.textContent)
    expect(rows).toEqual(['databaseReady', 'redisReady', 'storageNot ready'])
  })

  it('tells the user how to start the API when it cannot be reached', async () => {
    respond({})
    renderApp('/settings')
    expect(await screen.findByRole('alert')).toHaveTextContent('make dev')
  })
})
