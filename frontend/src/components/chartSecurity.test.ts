import { describe, expect, it } from 'vitest'
import source from './ChartCanvas.tsx?raw'

// M19 security review (docs/SECURITY.md, "Dependencies"): ECharts below 6.1.0 has an XSS in the tooltip of the
// "lines" series (GHSA-fgmj-fm8m-jvvx). The app is not exposed while it registers only bar and line charts and
// leaves tooltips to ECharts' own escaping formatter (chart labels come from customer data).
describe('ChartCanvas stays outside the ECharts tooltip XSS', () => {
  it('registers only the bar and line series', () => {
    const imported = /import \{([^}]*)\} from 'echarts\/charts'/.exec(source)?.[1] ?? ''
    expect(
      imported
        .split(',')
        .map((name) => name.trim())
        .filter(Boolean)
        .sort(),
    ).toEqual(['BarChart', 'LineChart'])
    expect(source).not.toMatch(/LinesChart|type:\s*'lines'/)
  })

  it('never writes its own tooltip HTML', () => {
    expect(source).not.toMatch(/tooltip:\s*\{[^}]*\bformatter\s*:/s)
    expect(source).not.toMatch(/renderMode|innerHTML|dangerouslySetInnerHTML/)
  })
})
