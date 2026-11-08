import type { Meta, StoryObj } from '@storybook/react-vite'
import { SourceMark } from '../components/SourceMark'
import { now, ordersSource } from './fixtures'
import { dark, phone } from './variants'

const meta = {
  title: 'Signature/SourceMark',
  component: SourceMark,
  args: { index: 1, source: ordersSource, now },
  render: (args) => (
    <p className="text-body">
      South revenue was ₹28.0 L in Q3
      <SourceMark {...args} />
    </p>
  ),
} satisfies Meta<typeof SourceMark>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
