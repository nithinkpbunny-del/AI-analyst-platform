import type { ReactNode } from 'react'

export function PageHeader({
  title,
  intro,
  children,
}: {
  title: string
  intro?: string
  children?: ReactNode
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-prose">
        <h1 className="text-h2 tablet:text-h1">{title}</h1>
        {intro ? <p className="m-0 mt-2 text-body text-ink-muted">{intro}</p> : null}
      </div>
      {children}
    </header>
  )
}
