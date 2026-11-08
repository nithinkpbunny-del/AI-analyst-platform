export interface Tokens {
  color: Record<'light' | 'dark', Record<string, string>>
  chart: Record<'light' | 'dark', string[]>
  haldiMarkOpacity: Record<'light' | 'dark', number>
  [key: string]: unknown
}
export function loadTokens(): Tokens
export function buildTokensCss(tokens: Tokens): string
export function buildTailwindTheme(tokens: Tokens): string
export function outputs(tokens?: Tokens): Record<string, string>
