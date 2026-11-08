import type { Meta, StoryObj } from '@storybook/react-vite'
import { Highlight } from '../components/Highlight'
import { dark, phone } from './variants'

const meta = {
  title: 'Signature/Highlight',
  component: Highlight,
  args: { children: '−18%', sweep: true },
  render: (args) => (
    <p className="font-display text-h2 font-semibold">
      South revenue fell <Highlight {...args} /> in Q3.
    </p>
  ),
} satisfies Meta<typeof Highlight>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
export const WithoutSweep: Story = { args: { sweep: false } }
export const Hindi: Story = {
  render: (args) => (
    <p lang="hi" className="font-display text-h2 font-semibold">
      दक्षिण में बिक्री <Highlight {...args} /> घटी।
    </p>
  ),
}
