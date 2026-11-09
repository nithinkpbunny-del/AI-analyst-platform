import type { Meta, StoryObj } from '@storybook/react-vite'
import { useState } from 'react'
import { Button } from '../components/ui/button'
import { ConfirmDialog } from '../components/ui/dialog'
import { dark, phone } from './variants'

function RemoveSample() {
  const [open, setOpen] = useState(true)
  return (
    <>
      <Button variant="secondary" onClick={() => setOpen(true)}>
        Remove Ravi Kumar
      </Button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Remove Ravi Kumar?"
        description="Ravi Kumar loses access to UrbanCart at once and is signed out of it on every device. You can invite them again later."
        confirmLabel="Remove Ravi Kumar"
        onConfirm={() => setOpen(false)}
      />
    </>
  )
}

const meta = {
  title: 'Primitives/Dialog',
  component: RemoveSample,
} satisfies Meta<typeof RemoveSample>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
