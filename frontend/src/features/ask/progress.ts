import type { TFunction } from 'i18next'
import type { Step } from '../../components/ProgressSteps'
import type { ChatMessage } from '../../lib/chatApi'
import { progressText } from './text'

/** Steps while the answer is made: every step but the last is ticked. */
export function progressSteps(t: TFunction, message: ChatMessage): Step[] {
  const steps = message.progress.filter((s) => s.code !== 'done')
  return steps.map((step, i) => ({
    id: `${i}-${step.code}`,
    label: progressText(t, step),
    status: i < steps.length - 1 || message.status === 'done' ? 'done' : 'active',
  }))
}
