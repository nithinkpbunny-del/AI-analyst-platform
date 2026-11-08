import type { Meta, StoryObj } from '@storybook/react-vite'
import tokens from '../../../design/tokens.json'
import { dark, phone } from './variants'

function Foundations() {
  const names = Object.keys(tokens.color.light)
  const sizes = Object.entries(tokens.fontSize)
  return (
    <div className="space-y-8">
      <section>
        <h2 className="text-h3">Colour</h2>
        <ul className="m-0 mt-4 grid list-none grid-cols-2 gap-3 p-0 tablet:grid-cols-4">
          {names.map((name) => (
            <li key={name} className="rounded-control border border-line bg-surface p-3">
              <span
                className="block h-10 rounded-sheet border border-line"
                style={{ background: `var(--${name})` }}
              />
              <span className="mt-2 block text-small font-medium">{name}</span>
            </li>
          ))}
        </ul>
        <h3 className="mt-6 text-h4">Chart series</h3>
        <div className="mt-2 flex gap-2">
          {tokens.chart.light.map((_, i) => (
            <span
              key={i}
              className="flex h-10 flex-1 items-end rounded-sheet p-1 text-caption text-surface"
              style={{ background: `var(--chart-${i + 1})` }}
            >
              {i + 1}
            </span>
          ))}
        </div>
      </section>
      <section>
        <h2 className="text-h3">Type</h2>
        <ul className="m-0 mt-4 list-none space-y-3 p-0">
          {sizes.map(([name, size]) => (
            <li key={name} className="flex items-baseline gap-4 border-t border-line pt-3">
              <span className="w-20 shrink-0 text-caption text-ink-muted">
                {name} · {size}
              </span>
              <span
                className={
                  name === 'body' || name === 'small' || name === 'caption'
                    ? ''
                    : 'font-display font-semibold'
                }
                style={{ fontSize: size }}
              >
                Revenue ₹12.4 L
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-6 space-y-2 text-body">
          <p lang="hi">हिन्दी: इस हफ़्ते बिक्री ₹12.4 लाख रही।</p>
          <p lang="kn">ಕನ್ನಡ: ಈ ವಾರದ ಮಾರಾಟ ₹12.4 ಲಕ್ಷ.</p>
          <p lang="ta">தமிழ்: இந்த வாரம் விற்பனை ₹12.4 லட்சம்.</p>
          <p lang="te">తెలుగు: ఈ వారం అమ్మకాలు ₹12.4 లక్షలు.</p>
          <p lang="ml">മലയാളം: ഈ ആഴ്ചത്തെ വിൽപ്പന ₹12.4 ലക്ഷം.</p>
          <p lang="bn">বাংলা: এই সপ্তাহে বিক্রি ₹12.4 লাখ।</p>
          <p lang="gu">ગુજરાતી: આ અઠવાડિયે વેચાણ ₹12.4 લાખ.</p>
          <p lang="mr">मराठी: या आठवड्यात विक्री ₹12.4 लाख.</p>
        </div>
      </section>
      <section>
        <h2 className="text-h3">Radius by hierarchy</h2>
        <div className="mt-4 flex flex-wrap items-center gap-4 text-small">
          <span className="rounded-panel border border-line bg-surface p-4">Panel 16 px</span>
          <span className="rounded-control border border-line-strong bg-surface p-4">
            Control 10 px
          </span>
          <span className="rounded-pill bg-neel-soft px-4 py-2 text-neel">Chip · pill</span>
          <span className="rounded-sheet border border-line bg-surface p-4">Answer sheet 4 px</span>
        </div>
      </section>
    </div>
  )
}

const meta = { title: 'Foundations/Tokens', component: Foundations } satisfies Meta<
  typeof Foundations
>
export default meta
type Story = StoryObj<typeof meta>

export const Light: Story = {}
export const Dark: Story = { ...dark }
export const Phone: Story = { ...phone }
