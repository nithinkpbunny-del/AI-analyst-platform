import { useTranslation } from 'react-i18next'

const SITE_URL: string = import.meta.env.VITE_SITE_BASE_URL ?? 'http://localhost:4321'

/** "I agree to the privacy policy and terms." with links to the marketing site (BUILD_SPEC 6.6). */
export function ConsentLabel() {
  const { t } = useTranslation()
  return (
    <>
      {t('auth.signUp.consentBefore')}{' '}
      <a href={`${SITE_URL}/privacy`} target="_blank" rel="noreferrer">
        {t('auth.signUp.privacy')}
      </a>{' '}
      {t('auth.signUp.and')}{' '}
      <a href={`${SITE_URL}/terms`} target="_blank" rel="noreferrer">
        {t('auth.signUp.terms')}
      </a>
      .
    </>
  )
}
