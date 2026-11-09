import type { Meta, StoryObj } from '@storybook/react-vite'
import { MemoryRouter } from 'react-router'
import { AppRoutes } from '../app/AppRoutes'
import { Providers } from '../app/Providers'
import { demoSession } from '../lib/demoSession'
import { dark, phone } from './variants'

function Shell({ path }: { path: string }) {
  return (
    <div className="-m-4">
      <Providers session={demoSession}>
        <MemoryRouter initialEntries={[path]}>
          <AppRoutes />
        </MemoryRouter>
      </Providers>
    </div>
  )
}

const meta = {
  title: 'App/Shell',
  component: Shell,
  args: { path: '/today' },
} satisfies Meta<typeof Shell>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
export const Tablet: Story = { globals: { viewport: { value: 'tablet', isRotated: false } } }
