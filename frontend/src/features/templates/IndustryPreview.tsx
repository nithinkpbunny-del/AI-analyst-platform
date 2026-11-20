import { TemplatePreview } from './TemplatePreview'
import { findTemplate, useTemplates } from './useTemplates'

/** The preview under the "Kind of business" choice in Settings. Shows nothing until the templates are known. */
export default function IndustryPreview({ industry }: { industry: string }) {
  const templates = useTemplates()
  const found = findTemplate(templates.data, industry)
  return found ? <TemplatePreview template={found} /> : null
}
