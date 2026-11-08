#!/usr/bin/env node
// BUILD_SPEC 11a.9: the app shell's first load must be under 200 KB of gzipped JavaScript.
// Measures the scripts referenced by dist/index.html (run after `npm run build`).
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { gzipSync } from 'node:zlib'

const LIMIT = 200 * 1024
const dist = new URL('../dist/', import.meta.url).pathname
const html = readFileSync(join(dist, 'index.html'), 'utf8')
const scripts = [...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.js)"/g)].map((m) => m[1])
let total = 0
for (const file of scripts) {
  const size = gzipSync(readFileSync(join(dist, file))).length
  total += size
  console.log(`${file}: ${(size / 1024).toFixed(1)} KB gzipped`)
}
console.log(`total: ${(total / 1024).toFixed(1)} KB of ${LIMIT / 1024} KB`)
if (scripts.length === 0 || total > LIMIT) process.exit(1)
