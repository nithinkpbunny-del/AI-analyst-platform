import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { useSession } from '../../lib/authContext'
import { DICTIONARY, dictionaryApi } from '../../lib/dictionaryApi'

/** Tells an Owner that a dictionary draft is waiting (after a new connection or a change in the data). */
export function DraftNotice() {
  const { t } = useTranslation()
  const { permissions } = useSession()
  const canEdit = permissions.includes('dictionary.edit')
  const query = useQuery({
    queryKey: DICTIONARY,
    queryFn: dictionaryApi.get,
    enabled: canEdit,
    retry: false,
    refetchInterval: (q) => (q.state.data?.drafting ? 2500 : false),
  })
  if (!canEdit || !query.data?.draft) return null
  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-3 rounded-panel border border-caution bg-caution-soft p-4 text-small"
    >
      <span>{t('dictionary.notice.draftWaiting')}</span>
      <Link to="/dictionary" className="font-medium text-neel underline underline-offset-4">
        {t('dictionary.notice.review')}
      </Link>
    </div>
  )
}
