import type { Meta, StoryObj } from '@storybook/react-vite'
import { fn } from 'storybook/test'
import { AnswerSheet } from '../components/AnswerSheet'
import { southEvidence, southLead } from './fixtures'
import { dark, phone } from './variants'

const meta = {
  title: 'Signature/AnswerSheet',
  component: AnswerSheet,
  args: {
    lead: southLead,
    evidence: southEvidence,
    whyItMatters:
      'The South is your largest region: at this pace Q4 revenue there would be about ₹9 L lower.',
    dataNotes: [
      'Website orders for 9–15 March 2026 are missing, so March is slightly understated.',
    ],
    nextQuestions: [
      'Which Electronics products lost the most orders?',
      'What if we undo the price rise?',
    ],
    onNextQuestion: fn(),
    onSave: fn(),
    onPin: fn(),
    onExport: fn(),
    onShare: fn(),
    onShowWork: fn(),
  },
} satisfies Meta<typeof AnswerSheet>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
