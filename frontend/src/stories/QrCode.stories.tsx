import type { Meta, StoryObj } from '@storybook/react-vite'
import { QrCode } from '../components/QrCode'
import { dark, phone } from './variants'

// A real 25×25 code (version 2) so the story looks like what authenticator apps scan.
const ROWS = [
  '1111111011001010101111111',
  '1000001001110100101000001',
  '1011101010011010101011101',
  '1011101000101101001011101',
  '1011101011010010101011101',
  '1000001010101011001000001',
  '1111111010101010101111111',
  '0000000011010100100000000',
  '1100111100110011011100101',
  '0101000001001110100011010',
  '1011011110010101011010111',
  '0100100011101010110001001',
  '1110011100010101001110110',
  '0011100001101011010101001',
  '1101011110011010101001111',
  '0010100100100101010010001',
  '1010111010111010111110101',
  '0000000010100101100011010',
  '1111111001011010101010111',
  '1000001011100101100010001',
  '1011101000011010111110110',
  '1011101011100101001001001',
  '1011101000111010110101111',
  '1000001011000101001010001',
  '1111111010110010101101011',
]

const meta = {
  title: 'Components/QrCode',
  component: QrCode,
  args: { rows: ROWS, label: 'QR code for your authenticator app' },
} satisfies Meta<typeof QrCode>
export default meta
type Story = StoryObj<typeof meta>

/** Always dark on light, even in dark mode: authenticator apps cannot read inverted codes. */
export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
