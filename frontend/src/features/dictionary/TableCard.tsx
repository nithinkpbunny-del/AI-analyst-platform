import { Check, ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Input } from '../../components/ui/input'
import { Select } from '../../components/ui/select'
import { cn } from '../../lib/cn'
import {
  ROLES,
  type DictionaryColumn,
  type DictionaryRole,
  type DictionaryTable,
  type DictionaryUnit,
} from '../../lib/dictionaryApi'
import { evidenceText, reasonText } from './text'

function unitValue(column: DictionaryColumn): string {
  return column.unit ?? ''
}

/** Words separated by commas. The text is kept as typed, so a comma can be typed before the next word. */
function SynonymsInput({
  words,
  label,
  placeholder,
  className,
  onChange,
}: {
  words: string[]
  label: string
  placeholder: string
  className?: string
  onChange: (words: string[]) => void
}) {
  const [text, setText] = useState(words.join(', '))
  return (
    <Input
      className={className}
      value={text}
      aria-label={label}
      placeholder={placeholder}
      onChange={(event) => {
        setText(event.target.value)
        onChange(
          event.target.value
            .split(',')
            .map((word) => word.trim())
            .filter(Boolean)
            .slice(0, 12),
        )
      }}
    />
  )
}

function ColumnRow({
  name,
  column,
  editable,
  onChange,
}: {
  name: string
  column: DictionaryColumn
  editable: boolean
  onChange: (next: DictionaryColumn) => void
}) {
  const { t } = useTranslation()
  const reason = reasonText(t, column.review_reason)
  const evidence = evidenceText(t, column.evidence)
  const typeNote = column.type
  return (
    <li
      className={cn(
        'flex flex-col gap-3 border-b border-line py-4 last:border-b-0',
        column.needs_review && 'rounded-control border-l-4 border-l-caution bg-caution-soft px-3',
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="m-0 text-small font-medium">
          <span className="font-mono">{name}</span>{' '}
          <span className="text-ink-muted">· {typeNote}</span>
        </p>
        {column.source === 'owner' ? (
          <span className="text-caption text-ink-muted">{t('dictionary.column.edited')}</span>
        ) : null}
      </div>
      {editable ? (
        <div className="grid gap-3 tablet:grid-cols-[2fr_1fr_1fr]">
          <Input
            value={column.meaning}
            maxLength={300}
            aria-label={t('dictionary.column.aria.meaning', { name })}
            onChange={(event) => onChange({ ...column, meaning: event.target.value })}
          />
          <Select
            value={column.role}
            aria-label={t('dictionary.column.aria.role', { name })}
            onChange={(event) =>
              onChange({ ...column, role: event.target.value as DictionaryRole })
            }
          >
            {ROLES.map((role) => (
              <option key={role} value={role}>
                {t(`dictionary.roles.${role}`)}
              </option>
            ))}
          </Select>
          <Select
            value={unitValue(column)}
            aria-label={t('dictionary.column.aria.unit', { name })}
            onChange={(event) => {
              const unit = (event.target.value || null) as DictionaryUnit | null
              onChange({ ...column, unit, divide_by: unit === 'INR' ? column.divide_by : null })
            }}
          >
            <option value="">{t('dictionary.units.none')}</option>
            <option value="INR">{t('dictionary.units.INR')}</option>
            <option value="percent">{t('dictionary.units.percent')}</option>
          </Select>
          <SynonymsInput
            className="tablet:col-span-2"
            words={column.synonyms}
            label={t('dictionary.column.aria.synonyms', { name })}
            placeholder={t('dictionary.column.synonymsHint')}
            onChange={(synonyms) => onChange({ ...column, synonyms })}
          />
          {column.unit === 'INR' ? (
            <label className="flex min-h-11 items-center gap-2 text-small">
              <input
                type="checkbox"
                className="size-5"
                checked={column.divide_by === 100}
                aria-label={t('dictionary.column.aria.paise', { name })}
                onChange={(event) =>
                  onChange({ ...column, divide_by: event.target.checked ? 100 : null })
                }
              />
              {t('dictionary.column.paise')}
            </label>
          ) : null}
        </div>
      ) : (
        <dl className="m-0 grid gap-1 text-small tablet:grid-cols-[2fr_1fr_1fr]">
          <dd className="m-0">{column.meaning}</dd>
          <dd className="m-0 text-ink-muted">{t(`dictionary.roles.${column.role}`)}</dd>
          <dd className="m-0 text-ink-muted">
            {column.unit ? t(`dictionary.units.${column.unit}`) : ''}
            {column.divide_by ? ` · ${t('dictionary.column.paise')}` : ''}
          </dd>
        </dl>
      )}
      {column.needs_review ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="m-0 max-w-prose text-small text-caution">
            {reason}
            {evidence ? <span className="block text-ink-muted">{evidence}</span> : null}
          </p>
          {editable ? (
            <button
              type="button"
              onClick={() => onChange({ ...column, needs_review: false, review_reason: null })}
              aria-label={`${t('dictionary.column.check')}: ${name}`}
              className="inline-flex min-h-11 items-center gap-2 rounded-control border border-line-strong bg-surface px-4 text-small font-medium hover:bg-neel-soft"
            >
              <Check className="size-4" aria-hidden />
              {t('dictionary.column.check')}
            </button>
          ) : null}
        </div>
      ) : evidence && editable ? (
        <p className="m-0 text-caption text-ink-muted">{evidence}</p>
      ) : null}
    </li>
  )
}

/** One table of the dictionary: what a row is, its date column and every column. Read-only unless `onChange`. */
export function TableCard({
  tableKey,
  table,
  onChange,
  defaultOpen = false,
}: {
  tableKey: string
  table: DictionaryTable
  onChange?: (next: DictionaryTable) => void
  defaultOpen?: boolean
}) {
  const { t } = useTranslation()
  const editable = onChange !== undefined
  const names = Object.keys(table.columns)
  const toCheck = names.filter((name) => table.columns[name]!.needs_review).length
  const timeColumns = names.filter((name) => table.columns[name]!.role === 'time')
  return (
    <details
      open={defaultOpen || toCheck > 0}
      className="group rounded-panel border border-line bg-surface"
      data-table={tableKey}
    >
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 p-4 tablet:px-6">
        <span className="flex flex-col">
          <span className="text-h4">{table.name}</span>
          <span className="text-small text-ink-muted">
            {t('dictionary.tables.columns', { count: names.length })}
            {table.connection_name
              ? ` · ${t('dictionary.tables.from', { name: table.connection_name })}`
              : ''}
          </span>
        </span>
        <span className="flex items-center gap-3">
          {toCheck > 0 ? (
            <span className="rounded-full bg-caution-soft px-3 py-1 text-caption text-caution">
              {t('dictionary.tables.toCheck', { count: toCheck })}
            </span>
          ) : null}
          <ChevronDown
            className="size-4 text-ink-muted transition-transform group-open:rotate-180"
            aria-hidden
          />
        </span>
      </summary>
      <div className="border-t border-line p-4 tablet:p-6">
        <div className="grid gap-3 tablet:grid-cols-[2fr_1fr]">
          {editable ? (
            <>
              <label className="flex flex-col gap-1.5 text-small font-medium">
                {t('dictionary.tables.meaning')}
                <Input
                  value={table.meaning}
                  maxLength={300}
                  onChange={(event) => onChange({ ...table, meaning: event.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1.5 text-small font-medium">
                {t('dictionary.tables.timeColumn')}
                <Select
                  value={table.time_column ?? ''}
                  onChange={(event) =>
                    onChange({ ...table, time_column: event.target.value || null })
                  }
                >
                  <option value="">{t('dictionary.tables.noTimeColumn')}</option>
                  {timeColumns.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </Select>
              </label>
            </>
          ) : (
            <p className="m-0 text-small">{table.meaning}</p>
          )}
        </div>
        <ul className="m-0 mt-2 list-none p-0">
          {names.map((name) => (
            <ColumnRow
              key={name}
              name={name}
              column={table.columns[name]!}
              editable={editable}
              onChange={(next) =>
                onChange?.({ ...table, columns: { ...table.columns, [name]: next } })
              }
            />
          ))}
        </ul>
      </div>
    </details>
  )
}
