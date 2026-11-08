import type { Meta, StoryObj } from '@storybook/react-vite'
import { useState } from 'react'
import { fn } from 'storybook/test'
import { ScenarioCard, type ScenarioCardProps } from '../components/ScenarioCard'
import { dark, phone } from './variants'

// Illustrative maths only: the real what-if engine (M9) computes these from the company's history.
function Interactive(props: ScenarioCardProps) {
  const [discount, setDiscount] = useState(props.lever.value)
  const uplift = 1 + discount * 0.012
  return (
    <ScenarioCard
      {...props}
      lever={{ ...props.lever, value: discount }}
      onLeverChange={setDiscount}
      scenario={{
        expected: props.baseline * uplift,
        low: props.baseline * (uplift - 0.04),
        high: props.baseline * (uplift + 0.035),
      }}
    />
  )
}

const meta = {
  title: 'Signature/ScenarioCard',
  component: ScenarioCard,
  args: {
    title: 'Extra discount on Fashion in October',
    horizon: 'next 3 months',
    lever: {
      label: 'Extra discount',
      value: 10,
      min: 0,
      max: 30,
      step: 1,
      format: (v: number) => `${v}%`,
    },
    onLeverChange: fn(),
    baseline: 1_86_00_000,
    scenario: { expected: 2_08_00_000, low: 2_00_00_000, high: 2_15_00_000 },
    assumptions: [
      'Fashion orders rise about 1.2% for each extra point of discount (from your Oct 2025 sale)',
      'Marketing spend stays at ₹6.6 L a month',
      'Repeat customers react like new ones',
    ],
    onEditAssumptions: fn(),
    confidence: {
      level: 'medium',
      reason: 'Based on one past festive discount; ranges are wider than usual.',
    },
  },
  render: (args) => (
    <div className="max-w-xl">
      <Interactive {...args} />
    </div>
  ),
} satisfies Meta<typeof ScenarioCard>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
