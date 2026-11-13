/** A link that opens Ask with a question typed in; `run` asks it at once (Today, the first health check). */
export function askLink(question: string, run = false): string {
  return `/ask?q=${encodeURIComponent(question)}${run ? '&run=1' : ''}`
}

export const TODAY = ['today']
