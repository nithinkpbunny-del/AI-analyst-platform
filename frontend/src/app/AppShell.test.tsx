import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderApp } from '../test-utils'

beforeEach(() => {
  window.localStorage.clear()
  delete document.documentElement.dataset.theme
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
})

describe('app shell', () => {
  it('opens on Today', () => {
    renderApp('/')
    expect(screen.getByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument()
  })

  it('lists the main screens and, for Owners, the management screens in spec order', () => {
    renderApp()
    const nav = screen.getByTestId('side-nav')
    const labels = within(nav)
      .getAllByRole('link')
      .map((link) => link.textContent)
    expect(labels).toEqual([
      'Today',
      'Ask',
      'Dashboard',
      'What-if',
      'Goals',
      'Briefings',
      'Tasks',
      'Connections',
      'Team',
      'Activity',
      'Settings',
    ])
  })

  it('marks the current screen and navigates', async () => {
    renderApp()
    const nav = screen.getByTestId('side-nav')
    await userEvent.click(within(nav).getByRole('link', { name: 'Goals' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Goals' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Goals' })).toHaveAttribute('aria-current', 'page')
  })

  it('every placeholder screen has one sentence and one primary action', () => {
    renderApp('/briefings')
    const main = screen.getByRole('main')
    expect(
      within(main).getByText('Your first briefing arrives the morning after you connect data.'),
    ).toBeInTheDocument()
    expect(within(main).getByRole('link', { name: 'Connect data' })).toHaveAttribute(
      'href',
      '/connections',
    )
  })

  it('collapses the desktop sidebar and remembers it', async () => {
    renderApp()
    await userEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }))
    expect(screen.getByTestId('side-nav')).toHaveAttribute('data-collapsed', 'true')
    expect(window.localStorage.getItem('analyst.sidebarCollapsed')).toBe('1')
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()
  })

  it('has bottom tabs for phones with Today, Ask, Dashboard and More', async () => {
    renderApp()
    const tabs = screen.getByTestId('bottom-tabs')
    expect(
      within(tabs)
        .getAllByRole('link')
        .map((l) => l.textContent),
    ).toEqual(['Today', 'Ask', 'Dashboard'])
    await userEvent.click(within(tabs).getByRole('button', { name: 'More' }))
    const sheet = await screen.findByRole('dialog', { name: 'More' })
    expect(
      within(sheet)
        .getAllByRole('link')
        .map((l) => l.textContent),
    ).toEqual([
      'What-if',
      'Goals',
      'Briefings',
      'Tasks',
      'Connections',
      'Team',
      'Activity',
      'Settings',
    ])
    await userEvent.click(within(sheet).getByRole('link', { name: 'Team' }))
    expect(screen.getByRole('heading', { level: 1, name: 'Team' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the floating Ask button everywhere except on Ask', async () => {
    renderApp('/dashboard')
    await userEvent.click(screen.getByTestId('ask-fab'))
    expect(screen.getByRole('heading', { level: 1, name: 'Ask' })).toBeInTheDocument()
    expect(screen.queryByTestId('ask-fab')).not.toBeInTheDocument()
  })

  it('offers a skip link to the main content', () => {
    renderApp()
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')
  })
})

describe('theme', () => {
  it('follows the device by default and can be forced to dark or light', async () => {
    renderApp('/settings')
    expect(screen.getByRole('radio', { name: 'Match my device' })).toBeChecked()
    expect(document.documentElement.dataset.theme).toBeUndefined()

    await userEvent.click(screen.getByRole('radio', { name: 'Dark' }))
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('analyst.theme')).toBe('dark')

    await userEvent.click(screen.getByRole('radio', { name: 'Match my device' }))
    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(window.localStorage.getItem('analyst.theme')).toBeNull()
  })

  it('restores a saved choice', () => {
    window.localStorage.setItem('analyst.theme', 'light')
    renderApp('/settings')
    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked()
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})
