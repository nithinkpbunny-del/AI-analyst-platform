/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the API, e.g. http://localhost:8000 (set by docker-compose from API_BASE_URL). */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
