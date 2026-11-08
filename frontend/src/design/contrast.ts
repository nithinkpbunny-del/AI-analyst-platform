// WCAG 2.2 relative luminance and contrast ratio (https://www.w3.org/TR/WCAG22/#dfn-contrast-ratio).

function channel(value: number): number {
  const c = value / 255
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}

function rgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]
}

export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex)
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

/** Colour of `top` painted at `alpha` opacity over `bottom`. */
export function blend(top: string, bottom: string, alpha: number): string {
  const t = rgb(top)
  const b = rgb(bottom)
  return (
    '#' +
    t
      .map((v, i) =>
        Math.round(v * alpha + (b[i] ?? 0) * (1 - alpha))
          .toString(16)
          .padStart(2, '0'),
      )
      .join('')
  )
}
