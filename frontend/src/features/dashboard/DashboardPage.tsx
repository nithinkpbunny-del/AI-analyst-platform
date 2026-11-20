import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LayoutGrid, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState, type DragEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { EmptyState } from '../../components/EmptyState'
import { FormAlert } from '../../components/FormAlert'
import { PageHeader } from '../../components/PageHeader'
import { Button } from '../../components/ui/button'
import { ConfirmDialog } from '../../components/ui/dialog'
import { cn } from '../../lib/cn'
import { dashboardApi, type Dashboard, type Tile } from '../../lib/goalsApi'
import { useCan } from '../../lib/permissions'
import { TileCard } from './TileCard'
import { DASHBOARD } from './keys'
import { TileSettingsDialog } from './TileDialogs'

const POLL_MS = 1500

function moved(ids: string[], from: number, to: number): string[] {
  const next = [...ids]
  const [item] = next.splice(from, 1)
  if (item !== undefined) next.splice(to, 0, item)
  return next
}

/** ``/dashboard``: pinned answers as live tiles, re-run with your permissions (BUILD_SPEC X8, 11 "Dashboard"). */
export function DashboardPage() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const canAsk = useCan('ask')
  useEffect(() => {
    document.title = `${t('pages.dashboard.title')} · ${t('app.name')}`
  }, [t])
  const board = useQuery({
    queryKey: DASHBOARD,
    queryFn: dashboardApi.get,
    refetchInterval: (query) =>
      query.state.data?.tiles.some((tile) => tile.refreshing) ? POLL_MS : false,
  })
  const refresh = useMutation({
    mutationFn: (force: boolean) => dashboardApi.refresh({ force }),
    onSuccess: (data) => queryClient.setQueryData(DASHBOARD, data),
  })
  const started = useRef(false)
  useEffect(() => {
    // Opening the dashboard re-runs the tiles whose refresh time has passed.
    if (!started.current) {
      started.current = true
      refresh.mutate(false)
    }
  }, [refresh])
  const reorder = useMutation({
    mutationFn: (ids: string[]) => dashboardApi.reorder(ids),
    onMutate: (ids) => {
      const current = queryClient.getQueryData<Dashboard>(DASHBOARD)
      if (current) {
        const byId = new Map(current.tiles.map((tile) => [tile.id, tile]))
        const mine = ids.map((id) => byId.get(id)).filter((tile): tile is Tile => Boolean(tile))
        queryClient.setQueryData(DASHBOARD, {
          ...current,
          tiles: [...mine, ...current.tiles.filter((tile) => !tile.mine)],
        })
      }
    },
    onSuccess: (data) => queryClient.setQueryData(DASHBOARD, data),
  })
  const remove = useMutation({
    mutationFn: (tile: Tile) => dashboardApi.remove(tile.id),
    onSuccess: () => {
      setRemoving(null)
      setNotice(t('dashboard.remove.done'))
      void queryClient.invalidateQueries({ queryKey: DASHBOARD })
    },
  })
  const [settings, setSettings] = useState<Tile | null>(null)
  const [removing, setRemoving] = useState<Tile | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [announce, setAnnounce] = useState('')
  const [dragging, setDragging] = useState<string | null>(null)

  const data = board.data
  const mine = data?.tiles.filter((tile) => tile.mine) ?? []
  const team = data?.tiles.filter((tile) => !tile.mine) ?? []
  const ids = mine.map((tile) => tile.id)
  const busy = refresh.isPending || Boolean(data?.tiles.some((tile) => tile.refreshing))

  const move = (from: number, to: number) => {
    if (to < 0 || to >= ids.length || from === to) return
    const tile = mine[from]
    reorder.mutate(moved(ids, from, to))
    if (tile) setAnnounce(t('dashboard.moved', { title: tile.title, position: to + 1 }))
  }
  const onDrop = (event: DragEvent, to: number) => {
    event.preventDefault()
    const from = dragging ? ids.indexOf(dragging) : -1
    setDragging(null)
    if (from >= 0) move(from, to)
  }

  return (
    <div>
      <PageHeader title={t('pages.dashboard.title')} intro={t('pages.dashboard.intro')}>
        {data && data.tiles.length > 0 ? (
          <Button variant="secondary" disabled={busy} onClick={() => refresh.mutate(true)}>
            <RefreshCw aria-hidden strokeWidth={1.5} />
            {busy ? t('dashboard.refreshing') : t('dashboard.refreshAll')}
          </Button>
        ) : null}
      </PageHeader>
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
      {notice ? (
        <FormAlert tone="success" className="mb-4">
          {notice}
        </FormAlert>
      ) : null}
      {board.isError ? <FormAlert tone="error">{t('dashboard.loadFailed')}</FormAlert> : null}
      {board.isPending ? (
        <p className="m-0 text-small text-ink-muted">{t('dashboard.loading')}</p>
      ) : null}

      {data && data.tiles.length === 0 ? (
        <EmptyState
          className="max-w-2xl"
          icon={LayoutGrid}
          message={data.can_edit ? t('pages.dashboard.empty') : t('dashboard.emptyViewer')}
          action={
            canAsk ? (
              <Button asChild>
                <Link to="/ask">{t('pages.dashboard.action')}</Link>
              </Button>
            ) : undefined
          }
        />
      ) : null}

      {mine.length > 0 ? (
        <section aria-labelledby="tiles-mine" className="mb-8">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="tiles-mine" className="m-0 text-h4">
              {t('dashboard.yours')}
            </h2>
            <span className="text-small text-ink-muted tabular">
              {t('dashboard.count', { count: mine.length, limit: data?.limit ?? 30 })}
            </span>
          </div>
          {mine.length > 1 ? (
            <p className="m-0 mb-3 text-caption text-ink-muted">{t('dashboard.reorderHint')}</p>
          ) : null}
          <ol className="m-0 grid list-none grid-cols-1 gap-4 p-0 desktop:grid-cols-2">
            {mine.map((tile, index) => (
              <li
                key={tile.id}
                draggable
                onDragStart={(event) => {
                  setDragging(tile.id)
                  event.dataTransfer.effectAllowed = 'move'
                }}
                onDragEnd={() => setDragging(null)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => onDrop(event, index)}
                className={cn('min-w-0', dragging === tile.id && 'opacity-50')}
              >
                <TileCard
                  tile={tile}
                  highlight={index === 0}
                  actions={{
                    onMoveUp: index > 0 ? () => move(index, index - 1) : undefined,
                    onMoveDown: index < mine.length - 1 ? () => move(index, index + 1) : undefined,
                    onSettings: () => setSettings(tile),
                    onRemove: () => setRemoving(tile),
                  }}
                />
              </li>
            ))}
          </ol>
        </section>
      ) : null}

      {team.length > 0 ? (
        <section aria-labelledby="tiles-team">
          <h2 id="tiles-team" className="m-0 mb-3 text-h4">
            {t('dashboard.team')}
          </h2>
          <ul className="m-0 grid list-none grid-cols-1 gap-4 p-0 desktop:grid-cols-2">
            {team.map((tile, index) => (
              <li key={tile.id} className="min-w-0">
                <TileCard tile={tile} highlight={mine.length === 0 && index === 0} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {settings ? (
        <TileSettingsDialog
          key={settings.id}
          tile={settings}
          open
          onOpenChange={(open) => !open && setSettings(null)}
          onSaved={(text) => {
            setNotice(text)
            refresh.mutate(false)
          }}
        />
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t('dashboard.remove.title')}
        description={t('dashboard.remove.body')}
        confirmLabel={t('dashboard.remove.confirm')}
        busy={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing)}
      />
    </div>
  )
}
