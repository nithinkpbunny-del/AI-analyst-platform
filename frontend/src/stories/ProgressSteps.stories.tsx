import type { Meta, StoryObj } from '@storybook/react-vite'
import { ProgressSteps } from '../components/ProgressSteps'
import { dark, phone } from './variants'

const meta = {
  title: 'Signature/ProgressSteps',
  component: ProgressSteps,
  args: {
    steps: [
      { id: '1', label: 'Checking connected sources', status: 'done' },
      { id: '2', label: 'Fetching Shopify orders', status: 'done' },
      { id: '3', label: 'Comparing Q3 with Q2', status: 'active' },
      { id: '4', label: 'Writing the answer', status: 'pending' },
    ],
  },
} satisfies Meta<typeof ProgressSteps>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
