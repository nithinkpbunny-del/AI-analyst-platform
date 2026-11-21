import { useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { reportsApi, type BriefingRun } from '../../lib/reportsApi'
import { BRIEFINGS } from './keys'

const WORKING = new Set<BriefingRun['status']>(['queued', 'running'])
const POLL_MS = 3000

/**
 * "Send me a briefing now" (M19): the API queues the briefing and answers at once, so the button stays disabled
 * until that briefing is finished (sent, skipped or failed), not only while the request is open. Polls the
 * person's briefings list until it is done, then calls ``onFinished`` once and refreshes ``refresh``.
 */
export function useSendBriefingNow({
  onFinished,
  onError,
  refresh = [],
}: {
  onFinished: (run: BriefingRun) => void
  onError: (error: Error) => void
  refresh?: QueryKey[]
}) {
  const queryClient = useQueryClient()
  // The briefing this page asked for: its id and, once known, how it ended.
  const [asked, setAsked] = useState<BriefingRun | null>(null)
  const reported = useRef<string | null>(null)

  const send = useMutation({
    mutationFn: reportsApi.sendBriefingNow,
    onSuccess: (run) => setAsked(run),
    onError,
  })

  const waiting = asked !== null && WORKING.has(asked.status)
  const progress = useQuery({
    queryKey: ['briefing-now', asked?.id],
    queryFn: async () => {
      const { runs } = await reportsApi.briefings()
      return runs.find((run) => run.id === asked?.id) ?? null
    },
    enabled: waiting,
    refetchInterval: (query) => {
      const run = query.state.data
      return run && !WORKING.has(run.status) ? false : POLL_MS
    },
  })

  const latest = progress.data && progress.data.id === asked?.id ? progress.data : asked
  const done = latest !== null && !WORKING.has(latest.status)

  useEffect(() => {
    if (!done || latest === null || reported.current === latest.id) return
    reported.current = latest.id
    onFinished(latest)
    for (const key of [BRIEFINGS, ...refresh]) void queryClient.invalidateQueries({ queryKey: key })
  }, [done, latest, onFinished, queryClient, refresh])

  return {
    send: () => send.mutate(),
    busy: send.isPending || (latest !== null && !done),
  }
}
