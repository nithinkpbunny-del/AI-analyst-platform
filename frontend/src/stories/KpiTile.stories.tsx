import type { Meta, StoryObj } from '@storybook/react-vite'
import { KpiTile } from '../components/KpiTile'
import { dark, phone } from './variants'

const meta = {
  title: 'Signature/KpiTile',
  component: KpiTile,
  args: {
    label: 'Revenue this week',
    value: 1240000,
    format: 'inr',
    deltaPct: 12.4,
    comparison: 'last week',
    trend: [8.1, 8.6, 8.2, 9.4, 9.9, 10.8, 12.4],
    highlight: true,
  },
  render: (args) => (
    <div className="max-w-xs">
      <KpiTile {...args} />
    </div>
  ),
} satisfies Meta<typeof KpiTile>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
export const Falling: Story = {
  args: {
    label: 'South revenue, Q3',
    value: 2801265,
    deltaPct: -17.7,
    comparison: 'Q2',
    highlight: false,
  },
}
export const Row: Story = {
  render: () => (
    <div className="grid gap-4 tablet:grid-cols-3">
      <KpiTile
        label="Revenue"
        value={1240000}
        format="inr"
        deltaPct={12.4}
        comparison="last week"
        highlight
      />
      <KpiTile label="Orders" value={1683} deltaPct={3.1} comparison="last week" />
      <KpiTile
        label="Refunds"
        value={52}
        deltaPct={-8}
        comparison="last week"
        goodDirection="down"
      />
    </div>
  ),
}
