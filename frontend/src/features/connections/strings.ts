// The Connections screens' strings load with their code (DECISIONS D99). Import this from every module that shows
// `connections.*` text other than data notes (those are in the shell's locales/en.ts).
import { addStrings } from '../../lib/i18n'
import connections from '../../locales/en/connections'

addStrings(connections)
