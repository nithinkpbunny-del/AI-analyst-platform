import type { Meta, StoryObj } from '@storybook/react-vite'
import { Select, SelectField } from '../components/ui/select'
import { dark, phone } from './variants'

function SelectSample() {
  return (
    <div className="flex max-w-[440px] flex-col gap-5">
      <SelectField label="Role" defaultValue="analyst">
        <option value="analyst">Analyst</option>
        <option value="viewer">Viewer</option>
      </SelectField>
      <Select aria-label="Role for Ravi Kumar" defaultValue="owner" className="w-40">
        <option value="owner">Owner</option>
        <option value="analyst">Analyst</option>
        <option value="viewer">Viewer</option>
      </Select>
      <SelectField label="Disabled" disabled defaultValue="viewer">
        <option value="viewer">Viewer</option>
      </SelectField>
    </div>
  )
}

const meta = {
  title: 'Primitives/Select',
  component: SelectSample,
} satisfies Meta<typeof SelectSample>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
