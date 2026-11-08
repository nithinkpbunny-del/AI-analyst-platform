import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import en from '../locales/en'

// UI strings go through i18next from day one (BUILD_SPEC 3, R11): Phase 1 ships English only.
void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: 'en',
  fallbackLng: 'en',
  interpolation: { escapeValue: false },
  initAsync: false,
})

export default i18n
