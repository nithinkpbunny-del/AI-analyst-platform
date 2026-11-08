import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

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

describe('App', () => {
  it('shows the title and a loading state first', () => {
    respond({})
    render(<App />)
    expect(screen.getByRole('heading', { name: 'AI Business Analyst' })).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('Checking the API')
  })

  it('shows the API version and each service check when connected', async () => {
    respond({
      '/health': { status: 'ok', service: 'analyst-api', version: '0.1.0', env: 'development' },
      '/health/ready': { status: 'ok', checks: { database: 'ok', redis: 'ok', storage: 'failed' } },
    })
    render(<App />)
    expect(await screen.findByText(/API connected: version 0.1.0/)).toBeInTheDocument()
    expect(screen.getByText('database: ready')).toBeInTheDocument()
    expect(screen.getByText('storage: not ready')).toBeInTheDocument()
  })

  it('tells the user how to start the API when it cannot be reached', async () => {
    respond({})
    render(<App />)
    expect(await screen.findByRole('alert')).toHaveTextContent('make dev')
  })
})
