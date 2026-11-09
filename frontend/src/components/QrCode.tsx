import { useMemo } from 'react'
import { cn } from '../lib/cn'

const QUIET_ZONE = 4 // modules of white space around the code, as the QR standard asks

/**
 * Draws a QR code from its modules (rows of "0"/"1" computed by the API), so the secret it encodes never goes to
 * an image service. Always dark on light (`qr-dark` on `qr-light`) in both themes: scanners cannot read
 * inverted codes.
 */
export function QrCode({
  rows,
  label,
  className,
}: {
  rows: string[]
  label: string
  className?: string
}) {
  const size = rows.length + QUIET_ZONE * 2
  const path = useMemo(
    () =>
      rows
        .flatMap((row, y) =>
          [...row].map((cell, x) =>
            cell === '1' ? `M${x + QUIET_ZONE},${y + QUIET_ZONE}h1v1h-1z` : '',
          ),
        )
        .join(''),
    [rows],
  )
  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${size} ${size}`}
      shapeRendering="crispEdges"
      className={cn('block size-48 rounded-sheet border border-line', className)}
    >
      <rect width={size} height={size} className="fill-qr-light" />
      <path d={path} className="fill-qr-dark" />
    </svg>
  )
}
