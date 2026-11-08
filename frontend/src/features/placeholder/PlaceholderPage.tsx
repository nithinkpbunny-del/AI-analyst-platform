import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import type { NavItem } from '../../app/nav'
import { EmptyState } from '../../components/EmptyState'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'

const actionTarget: Record<string, string> = {
  'Connect data': '/connections',
  'Ask a question': '/ask',
  'Send invite': '/team',
}

/** Screen placeholder for the shell (M1b). Each screen is filled in by its own milestone. */
export function PlaceholderPage({ item }: { item: NavItem }) {
  const { t } = useTranslation()
  const title = t(`pages.${item.key}.title`)
  const action = t(`pages.${item.key}.action`)
  useEffect(() => {
    document.title = `${title} · ${t('app.name')}`
  }, [title, t])

  return (
    <>
      <PageHeader title={title} intro={t(`pages.${item.key}.intro`)} />
      <EmptyState
        className="max-w-2xl"
        icon={item.icon}
        message={t(`pages.${item.key}.empty`)}
        action={
          <Button asChild>
            <Link to={actionTarget[action] ?? '/today'}>{action}</Link>
          </Button>
        }
      />
    </>
  )
}
