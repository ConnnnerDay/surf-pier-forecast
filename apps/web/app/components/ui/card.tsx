import type { HTMLAttributes } from 'react'
import { cx } from './cx'

export type CardProps = HTMLAttributes<HTMLDivElement>

/** Surface container primitive (sprint 27, replacing v2/frontend's
 * `.card` global CSS class -- see R1_RECONCILIATION_AUDIT.md §3.2).
 */
export function Card({ className, ...props }: CardProps) {
  return (
    <div
      className={cx(
        'rounded-lg border border-border bg-surface p-4',
        className,
      )}
      {...props}
    />
  )
}
