import type { Meta, StoryObj } from '@storybook/react-vite'
import { Button } from '../components/ui/button'
import { dark, phone } from './variants'

const meta = {
  title: 'Primitives/Button',
  component: Button,
  render: () => (
    <div className="flex flex-wrap gap-3">
      <Button>Run scenario</Button>
      <Button variant="secondary">Export PDF</Button>
      <Button variant="ghost">Cancel</Button>
      <Button variant="link">See how it works</Button>
      <Button disabled>Send invite</Button>
    </div>
  ),
} satisfies Meta<typeof Button>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
