import { useQuery } from '@tanstack/react-query'
import { teamApi, type IndustryTemplate } from '../../lib/teamApi'

export const TEMPLATES = ['org-templates']

/** The industry templates: reference data, the same for everyone, so it is fetched once per visit. */
export function useTemplates(enabled = true) {
  return useQuery({
    queryKey: TEMPLATES,
    queryFn: teamApi.templates,
    staleTime: Infinity,
    retry: 1,
    enabled,
  })
}

export function findTemplate(
  templates: IndustryTemplate[] | undefined,
  key: string | null | undefined,
): IndustryTemplate | undefined {
  return templates?.find((template) => template.key === key)
}

/**
 * Starter questions for the Ask screen: the workspace's template questions once the kind of business is chosen
 * (and is not "something else"); ``null`` while unknown or when the generic questions should be used.
 */
export function useStarterQuestions(): string[] | null {
  const org = useQuery({ queryKey: ['organization'], queryFn: teamApi.organization })
  const industry = org.data?.industry
  const templates = useTemplates(Boolean(industry) && industry !== 'other')
  const found = findTemplate(templates.data, industry)
  return found && found.key !== 'other' ? found.starter_questions.slice(0, 4) : null
}
