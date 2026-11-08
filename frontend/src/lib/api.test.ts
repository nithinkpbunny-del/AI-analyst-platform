import { describe, expect, it, vi } from 'vitest'
import { getHealth, getReadiness } from './api'

function mockFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({ status, json: () => Promise.resolve(body) })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('api client', () => {
  it('reads the liveness document from /health', async () => {
    const fetchMock = mockFetch({
      status: 'ok',
      service: 'analyst-api',
      version: '0.1.0',
      env: 'test',
    })
    const health = await getHealth()
    expect(health.version).toBe('0.1.0')
    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/health$/)
  })

  it('returns the body of a 503 readiness response instead of throwing', async () => {
    mockFetch({ status: 'unavailable', checks: { database: 'failed', redis: 'ok' } }, 503)
    const readiness = await getReadiness()
    expect(readiness.status).toBe('unavailable')
    expect(readiness.checks.database).toBe('failed')
  })

  it('throws when the network is down', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(getHealth()).rejects.toThrow('Failed to fetch')
  })
})
