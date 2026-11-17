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

/**
 * Add a screen's strings (`locales/en/<screen>.ts`) when its code loads, so they are not part of the app shell's
 * first download (DECISIONS D99). Deep-merged: a screen may add keys under a section the shell already has.
 */
export function addStrings(bundle: Record<string, unknown>): void {
  i18n.addResourceBundle('en', 'translation', bundle, true, false)
}

export default i18n
