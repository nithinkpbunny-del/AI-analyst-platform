import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import type { ChartSpec } from '../lib/chatApi'
import { formatINR, formatNumber } from '../lib/format'

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, LegendComponent, SVGRenderer])

function token(name: string, fallback: string): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

function axisLabel(unit: string | null) {
  return (value: number) => (unit === 'INR' ? formatINR(value) : formatNumber(value))
}

/** ECharts, themed from the design tokens (BUILD_SPEC 11a.2). Loaded lazily by ChartRenderer. */
export default function ChartCanvas({ spec }: { spec: ChartSpec }) {
  const ink = token('--ink', '#1E1C3A')
  const muted = token('--ink-muted', '#5A5874')
  const line = token('--line', '#E2E4EE')
  const colors = [1, 2, 3, 4, 5, 6].map((n) => token(`--chart-${n}`, '#3A36A8'))
  const horizontal = spec.type === 'horizontal_bar'
  const stacked = spec.type === 'stacked_bar'
  const kind = spec.type === 'line' ? 'line' : 'bar'
  const categories = { type: 'category' as const, data: spec.x.values.map(String) }
  const values = {
    type: 'value' as const,
    axisLabel: { formatter: axisLabel(spec.unit), color: muted },
    splitLine: { lineStyle: { color: line } },
  }
  const reduced =
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
  const option = {
    // 11a.6: no motion for people who ask for none.
    animation: !reduced,
    color: colors,
    textStyle: { fontFamily: 'inherit', color: ink },
    grid: {
      left: 8,
      right: 16,
      top: spec.series.length > 1 ? 36 : 12,
      bottom: 8,
      containLabel: true,
    },
    legend:
      spec.series.length > 1 ? { top: 0, textStyle: { color: ink }, icon: 'roundRect' } : undefined,
    tooltip: {
      trigger: 'axis',
      valueFormatter: (value: number) =>
        spec.unit === 'INR' ? formatINR(value) : formatNumber(value),
    },
    xAxis: horizontal
      ? values
      : { ...categories, axisLabel: { color: muted }, axisLine: { lineStyle: { color: line } } },
    yAxis: horizontal ? { ...categories, axisLabel: { color: muted } } : values,
    series: spec.series.map((series, i) => ({
      name: series.name,
      type: kind,
      data: series.data,
      stack: stacked ? 'total' : undefined,
      symbol: kind === 'line' ? 'circle' : undefined,
      symbolSize: 6,
      // Never colour alone (11a.2): lines get distinct dash patterns.
      lineStyle:
        kind === 'line'
          ? { width: 2, type: (['solid', 'dashed', 'dotted'] as const)[i % 3] }
          : undefined,
      barMaxWidth: 32,
    })),
  }
  const box = useRef<HTMLDivElement>(null)
  const optionJson = JSON.stringify(option)
  useEffect(() => {
    const element = box.current
    if (!element) return
    const chart = echarts.init(element, undefined, { renderer: 'svg' })
    chart.setOption(JSON.parse(optionJson) as echarts.EChartsCoreOption)
    chart.setOption({
      xAxis: horizontal ? { axisLabel: { formatter: axisLabel(spec.unit) } } : {},
      yAxis: horizontal ? {} : { axisLabel: { formatter: axisLabel(spec.unit) } },
      tooltip: { valueFormatter: (value: number) => axisLabel(spec.unit)(value) },
    })
    const resize = new ResizeObserver(() => chart.resize())
    resize.observe(element)
    return () => {
      resize.disconnect()
      chart.dispose()
    }
  }, [optionJson, horizontal, spec.unit])
  return <div ref={box} style={{ height: 260, width: '100%' }} />
}
