import { describe, expect, it } from 'vitest'
import en from './en'
import connections from './en/connections'
import billing from './en/billing'
import dashboard from './en/dashboard'
import dictionary from './en/dictionary'
import goals from './en/goals'
import templates from './en/templates'

type Tree = { [key: string]: string | Tree }

function merge(target: Tree, source: Tree): Tree {
  for (const [key, value] of Object.entries(source)) {
    const current = target[key]
    if (typeof value === 'object' && typeof current === 'object') merge(current, value)
    else target[key] = value
  }
  return target
}

const all = [en, connections, dictionary, goals, dashboard, templates, billing].reduce<Tree>(
  (tree, bundle) => merge(tree, structuredClone(bundle) as Tree),
  {},
)

function has(path: string): boolean {
  const parts = path.split('.')
  let node: string | Tree | undefined = all
  for (const part of parts.slice(0, -1)) {
    node = typeof node === 'object' ? node[part] : undefined
  }
  if (typeof node !== 'object') return false
  const last = parts[parts.length - 1] ?? ''
  return last in node || `${last}_one` in node || `${last}_other` in node
}

// Every source file, as text (tests and stories excluded).
const sources = import.meta.glob(
  ['../**/*.ts', '../**/*.tsx', '!../**/*.test.*', '!../**/*.stories.*'],
  {
    query: '?raw',
    import: 'default',
    eager: true,
  },
) as Record<string, string>

describe('UI strings (DECISIONS D99)', () => {
  it('every literal key used in the code exists in locales/en.ts or a screen file', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(100)
    let seen = 0
    const missing: string[] = []
    for (const [file, text] of Object.entries(sources)) {
      for (const match of text.matchAll(/\bt\(\s*'([a-zA-Z][\w]*(?:\.[\w]+)+)'/g)) {
        seen += 1
        const key = match[1] ?? ''
        if (!has(key)) missing.push(`${file}: ${key}`)
      }
    }
    expect(seen).toBeGreaterThan(500)
    expect(missing).toEqual([])
  })

  it('the screen files are not part of the shell strings', () => {
    expect('add' in (en.connections as Tree)).toBe(false)
    expect('dictionary' in en).toBe(false)
    expect('goals' in en).toBe(false)
    expect('dashboard' in en).toBe(false)
    expect(Object.keys(en.connections)).toEqual(['quality'])
  })
})
