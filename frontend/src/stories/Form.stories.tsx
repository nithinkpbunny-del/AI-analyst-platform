import type { Meta, StoryObj } from '@storybook/react-vite'
import { useState } from 'react'
import { FormAlert } from '../components/FormAlert'
import { Button } from '../components/ui/button'
import { CheckboxField, Field } from '../components/ui/input'
import { dark, phone } from './variants'

function FormSample() {
  const [checked, setChecked] = useState(false)
  return (
    <div className="flex max-w-[440px] flex-col gap-5">
      <FormAlert tone="error">Invalid email or password.</FormAlert>
      <FormAlert tone="success">We sent a new link to asha@urbancart.in.</FormAlert>
      <FormAlert tone="info">
        You were signed out after 30 minutes without activity. Sign in again.
      </FormAlert>
      <Field label="Email" type="email" defaultValue="asha@urbancart.in" />
      <Field label="Email" type="email" defaultValue="asha" error="Enter a valid email address." />
      <Field
        label="Password"
        type="password"
        revealable
        hint="At least 10 characters. A short sentence works well."
      />
      <CheckboxField
        name="consent"
        checked={checked}
        onChange={setChecked}
        label="I agree to the privacy policy and terms."
        error={checked ? undefined : 'Accept the privacy policy and terms to create an account.'}
      />
      <Button className="w-full">Create account</Button>
    </div>
  )
}

const meta = { title: 'Primitives/Form', component: FormSample } satisfies Meta<typeof FormSample>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
