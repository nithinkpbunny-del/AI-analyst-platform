import type { Meta, StoryObj } from '@storybook/react-vite'
import { QualityBanner } from '../components/QualityBanner'
import { dark, phone } from './variants'

const meta = {
  title: 'Signature/QualityBanner',
  component: QualityBanner,
  args: { children: 'Website orders for 9–15 March 2026 are missing; March revenue may be low.' },
} satisfies Meta<typeof QualityBanner>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
