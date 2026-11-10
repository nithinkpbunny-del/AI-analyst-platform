import type { PickerConfig, Spreadsheet } from './connectionsApi'

// The Google Picker lets the person choose spreadsheets, so the app never needs to list their Drive
// (BUILD_SPEC 8.3). Its script is loaded only when the Picker is opened, and only with real Google credentials;
// in development the web app shows its own stand-in list instead.

interface PickerDoc {
  id: string
  name: string
}

interface PickerData {
  action: string
  docs?: PickerDoc[]
}

interface PickerBuilder {
  addView(view: unknown): PickerBuilder
  enableFeature(feature: unknown): PickerBuilder
  setOAuthToken(token: string): PickerBuilder
  setDeveloperKey(key: string): PickerBuilder
  setAppId(appId: string): PickerBuilder
  setTitle(title: string): PickerBuilder
  setCallback(callback: (data: PickerData) => void): PickerBuilder
  build(): { setVisible(visible: boolean): void }
}

interface GooglePickerNamespace {
  PickerBuilder: new () => PickerBuilder
  DocsView: new (viewId: unknown) => {
    setMimeTypes(types: string): unknown
    setMode(mode: unknown): unknown
  }
  ViewId: { SPREADSHEETS: unknown }
  DocsViewMode: { LIST: unknown }
  Feature: { MULTISELECT_ENABLED: unknown }
  Action: { PICKED: string; CANCEL: string }
}

declare global {
  interface Window {
    gapi?: { load(name: string, callback: () => void): void }
    google?: { picker?: GooglePickerNamespace }
  }
}

const SCRIPT = 'https://apis.google.com/js/api.js'
let loading: Promise<GooglePickerNamespace> | null = null

function loadPicker(): Promise<GooglePickerNamespace> {
  loading ??= new Promise((resolve, reject) => {
    const ready = () =>
      window.gapi?.load('picker', () => {
        const picker = window.google?.picker
        if (picker) resolve(picker)
        else reject(new Error('The Google Picker did not load.'))
      })
    if (window.gapi) {
      ready()
      return
    }
    const script = document.createElement('script')
    script.src = SCRIPT
    script.async = true
    script.onload = ready
    script.onerror = () => {
      loading = null
      reject(new Error('The Google Picker could not be loaded. Check your connection.'))
    }
    document.head.append(script)
  })
  return loading
}

/** Open the Picker; resolves with the chosen spreadsheets (empty when the person cancels). */
export async function pickSpreadsheets(
  config: PickerConfig,
  title: string,
): Promise<Spreadsheet[]> {
  const picker = await loadPicker()
  return new Promise((resolve) => {
    const view = new picker.DocsView(picker.ViewId.SPREADSHEETS)
    view.setMode(picker.DocsViewMode.LIST)
    new picker.PickerBuilder()
      .addView(view)
      .enableFeature(picker.Feature.MULTISELECT_ENABLED)
      .setOAuthToken(config.access_token)
      .setDeveloperKey(config.developer_key)
      .setAppId(config.app_id)
      .setTitle(title)
      .setCallback((data) => {
        if (data.action === picker.Action.PICKED) {
          resolve((data.docs ?? []).map((doc) => ({ id: doc.id, name: doc.name })))
        } else if (data.action === picker.Action.CANCEL) {
          resolve([])
        }
      })
      .build()
      .setVisible(true)
  })
}
