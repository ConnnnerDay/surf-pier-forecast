import type { HTMLAttributes } from 'react'
import { cx } from './cx'

export type ContainerProps = HTMLAttributes<HTMLDivElement>

/** Mobile-first responsive page-width wrapper (sprint 27's "gallery at
 * phone/desktop widths" acceptance bar) -- full width on phones,
 * capped and centered from tablet width up.
 */
export function Container({ className, ...props }: ContainerProps) {
  return (
    <div
      className={cx('mx-auto w-full max-w-3xl px-4 sm:px-6', className)}
      {...props}
    />
  )
}
