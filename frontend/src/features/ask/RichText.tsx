import { Fragment, type ReactNode } from 'react'
import { Highlight } from '../../components/Highlight'
import { SourceMark } from '../../components/SourceMark'
import { CITATION, type Sources } from './sources'

function withHighlight(text: string, figure: string | null | undefined): ReactNode {
  if (!figure) return text
  const at = text.indexOf(figure)
  if (at < 0) return text
  return (
    <>
      {text.slice(0, at)}
      <Highlight>{figure}</Highlight>
      {text.slice(at + figure.length)}
    </>
  )
}

/** Answer text with its citations as source marks; the key figure (lead only) gets the haldi mark. */
export function RichText({
  text,
  sources,
  keyFigure,
}: {
  text: string
  sources: Sources
  keyFigure?: string | null
}) {
  const parts: ReactNode[] = []
  let last = 0
  let highlighted = false
  for (const match of text.matchAll(CITATION)) {
    const before = text.slice(last, match.index)
    if (!highlighted && keyFigure && before.includes(keyFigure)) {
      parts.push(<Fragment key={`t${last}`}>{withHighlight(before, keyFigure)}</Fragment>)
      highlighted = true
    } else {
      parts.push(before)
    }
    for (const rid of (match[1] ?? '').split(',').map((r) => r.trim())) {
      const mark = sources.marks.get(rid)
      if (mark) {
        parts.push(
          <SourceMark key={`${match.index}-${rid}`} index={mark.index} source={mark.source} />,
        )
      }
    }
    last = (match.index ?? 0) + match[0].length
  }
  const rest = text.slice(last)
  parts.push(
    !highlighted && keyFigure ? (
      <Fragment key="rest">{withHighlight(rest, keyFigure)}</Fragment>
    ) : (
      rest
    ),
  )
  return <>{parts}</>
}
