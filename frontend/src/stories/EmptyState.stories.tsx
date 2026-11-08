import type { Meta, StoryObj } from '@storybook/react-vite'
import { Plug } from 'lucide-react'
import { EmptyState } from '../components/EmptyState'
import { Button } from '../components/ui/button'
import { dark, phone } from './variants'

const meta = {
  title: 'Signature/EmptyState',
  component: EmptyState,
  args: {
    icon: Plug,
    message: 'Connect your first data source to start asking questions.',
    action: <Button>Connect data</Button>,
  },
} satisfies Meta<typeof EmptyState>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
