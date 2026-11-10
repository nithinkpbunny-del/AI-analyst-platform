import type { Meta, StoryObj } from '@storybook/react-vite'
import { ConnectionCard } from '../components/ConnectionCard'
import { Button } from '../components/ui/button'
import { now } from './fixtures'
import { dark, phone } from './variants'

const meta = {
  title: 'Signature/ConnectionCard',
  component: ConnectionCard,
  args: {
    name: 'UrbanCart store',
    sourceType: 'Shopify',
    status: 'active',
    lastSuccessAt: new Date(now.getTime() - 3 * 60_000),
    now,
    actions: (
      <>
        <Button variant="secondary" size="sm">
          Test connection
        </Button>
        <Button variant="ghost" size="sm">
          Remove
        </Button>
      </>
    ),
  },
  render: (args) => (
    <div className="max-w-sm">
      <ConnectionCard {...args} />
    </div>
  ),
} satisfies Meta<typeof ConnectionCard>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
export const NeedsSignIn: Story = {
  args: {
    name: 'Targets 2026',
    sourceType: 'Google Sheets',
    status: 'needs_reauth',
    lastSuccessAt: new Date(now.getTime() - 26 * 3_600_000),
    message: 'Google stopped sharing this sheet. Sign in to Google again to reconnect.',
    actions: <Button size="sm">Reconnect Google Sheets</Button>,
  },
}
export const Reading: Story = {
  args: {
    name: 'UrbanCart orders',
    sourceType: 'Excel or CSV file',
    status: 'active',
    lastSuccessAt: null,
    reading: true,
    summary: '1 table · 28,540 rows',
    warnings: 2,
  },
}
export const Failing: Story = {
  args: {
    name: 'Payments',
    sourceType: 'Razorpay',
    status: 'error',
    message: 'Razorpay rejected the key. Paste a new key to reconnect.',
    actions: <Button size="sm">Update key</Button>,
  },
}
