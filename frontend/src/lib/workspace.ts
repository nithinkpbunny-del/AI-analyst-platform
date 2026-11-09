/** The last workspace used on this device, so the next sign-in opens it (not secret: just an id). */
const LAST_WORKSPACE_KEY = 'analyst.lastWorkspace'

export function rememberedWorkspace(): string | null {
  try {
    return window.localStorage.getItem(LAST_WORKSPACE_KEY)
  } catch {
    return null
  }
}

export function rememberWorkspace(id: string) {
  try {
    window.localStorage.setItem(LAST_WORKSPACE_KEY, id)
  } catch {
    /* storage blocked: the first workspace opens next time */
  }
}
