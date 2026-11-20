import { useTranslation } from 'react-i18next'
import type { IndustryTemplate } from '../../lib/teamApi'
import './strings'

/** What a template sets up: the numbers it watches and the questions it starts with (BUILD_SPEC X9). */
export function TemplatePreview({ template }: { template: IndustryTemplate }) {
  const { t } = useTranslation()
  return (
    <section
      aria-label={`${t('templates.previewTitle')}: ${template.name}`}
      className="rounded-panel border border-line p-4"
    >
      <p className="m-0 text-body">{template.summary}</p>
      <h3 className="m-0 mt-4 font-sans text-small font-semibold">{t('templates.watches')}</h3>
      <ul className="m-0 mt-1 list-none p-0">
        {template.kpis.map((kpi) => (
          <li key={kpi.key} className="border-b border-line py-2 last:border-b-0">
            <span className="text-body font-medium">{kpi.label}</span>
            <span className="block text-caption text-ink-muted">{kpi.meaning}</span>
          </li>
        ))}
      </ul>
      <h3 className="m-0 mt-4 font-sans text-small font-semibold">{t('templates.starters')}</h3>
      <ul className="m-0 mt-1 list-disc pl-5 text-body">
        {template.starter_questions.map((question) => (
          <li key={question} className="py-0.5">
            {question}
          </li>
        ))}
      </ul>
      <p className="m-0 mt-3 text-caption text-ink-muted">
        {t('templates.words', { words: template.words })}
      </p>
    </section>
  )
}
