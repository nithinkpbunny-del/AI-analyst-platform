import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { loadTokens, outputs } from '../../../design/build.mjs'
import { blend, contrast } from './contrast'

const tokens = loadTokens()
const TEXT = 4.5 // WCAG 2.2 AA body text
const UI = 3 // WCAG 2.2 AA large text, UI components and graphics

// [foreground, background, minimum]: every pairing the UI actually uses.
const pairs: [string, string, number][] = [
  ['ink', 'paper', TEXT],
  ['ink', 'surface', TEXT],
  ['ink', 'neel-soft', TEXT],
  ['ink-muted', 'paper', TEXT],
  ['ink-muted', 'surface', TEXT],
  ['ink-muted', 'neel-soft', TEXT],
  ['neel', 'paper', TEXT], // links
  ['neel', 'surface', TEXT],
  ['neel', 'neel-soft', TEXT], // active nav item
  ['on-neel', 'neel', TEXT], // primary buttons
  ['gain', 'paper', TEXT],
  ['gain', 'surface', TEXT],
  ['loss', 'paper', TEXT],
  ['loss', 'surface', TEXT],
  ['caution', 'paper', TEXT],
  ['caution', 'surface', TEXT],
  ['gain', 'gain-soft', TEXT], // status pills
  ['loss', 'loss-soft', TEXT],
  ['surface', 'loss', TEXT], // danger buttons (remove someone)
  ['caution', 'caution-soft', TEXT],
  ['ink', 'caution-soft', TEXT], // quality banner
  ['line-strong', 'surface', UI], // input and control borders
  ['line-strong', 'paper', UI],
  ['neel', 'paper', UI], // focus ring
  ['neel', 'surface', UI],
  // QR codes stay dark-on-light in both themes: authenticator apps cannot read inverted codes.
  ['qr-dark', 'qr-light', TEXT],
]

describe.each(['light', 'dark'] as const)('%s theme contrast (WCAG 2.2 AA)', (mode) => {
  const c = tokens.color[mode] as Record<string, string>

  it.each(pairs)('%s on %s ≥ %d:1', (fg, bg, min) => {
    expect(c[fg], fg).toBeDefined()
    expect(c[bg], bg).toBeDefined()
    expect(contrast(c[fg]!, c[bg]!)).toBeGreaterThanOrEqual(min)
  })

  it.each(['surface', 'paper'])('ink text over the haldi mark on %s ≥ 4.5:1', (bg) => {
    const mark = blend(c.haldi!, c[bg]!, tokens.haldiMarkOpacity[mode])
    expect(contrast(c.ink!, mark)).toBeGreaterThanOrEqual(TEXT)
  })

  it.each(tokens.chart[mode].map((colour, i) => [i + 1, colour]))(
    'chart series %d (%s) stands out from surface and paper ≥ 3:1',
    (_, colour) => {
      expect(contrast(colour as string, c.surface!)).toBeGreaterThanOrEqual(UI)
      expect(contrast(colour as string, c.paper!)).toBeGreaterThanOrEqual(UI)
    },
  )

  it('defines the same token names as the other theme', () => {
    expect(Object.keys(c).sort()).toEqual(Object.keys(tokens.color.light).sort())
  })
})

describe('generated design files', () => {
  it('are up to date with design/tokens.json (run `make tokens` if this fails)', () => {
    for (const [file, content] of Object.entries(outputs(tokens))) {
      const onDisk = readFileSync(join(__dirname, '../../../design', file), 'utf8')
      expect(onDisk, file).toBe(content)
    }
  })

  it('switches to dark on system preference unless light is chosen, and on data-theme="dark"', () => {
    const css = outputs(tokens)['generated/tokens.css']!
    expect(css).toContain('@media (prefers-color-scheme: dark)')
    expect(css).toContain(':root:not([data-theme="light"])')
    expect(css).toContain(':root[data-theme="dark"]')
  })

  it('removes Tailwind default colours so only tokens can be used', () => {
    expect(outputs(tokens)['generated/tailwind-theme.css']).toContain('--color-*: initial;')
  })
})
