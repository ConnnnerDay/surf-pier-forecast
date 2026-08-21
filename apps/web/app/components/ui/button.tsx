import Link from 'next/link'
import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from 'react'
import { cx } from './cx'

type ButtonVariant = 'primary' | 'secondary' | 'ghost'

const BASE_CLASSES =
  'inline-flex items-center justify-center gap-2 rounded-md px-5 py-3 font-semibold ' +
  'transition-colors focus-visible:outline-none focus-visible:ring-2 ' +
  'focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg ' +
  'disabled:cursor-not-allowed disabled:opacity-60'

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary: 'bg-primary text-primary-contrast hover:bg-primary-hover',
  secondary: 'border border-border bg-transparent text-primary hover:bg-surface',
  ghost: 'bg-transparent text-text hover:bg-surface',
}

type CommonProps = {
  variant?: ButtonVariant
  className?: string
}

type ButtonAsButton = CommonProps &
  ButtonHTMLAttributes<HTMLButtonElement> & { href?: undefined }

type ButtonAsLink = CommonProps &
  AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }

export type ButtonProps = ButtonAsButton | ButtonAsLink

/** Accessible button primitive (sprint 27's design-system gallery,
 * replacing v2/frontend's `.button` global CSS class -- see
 * R1_RECONCILIATION_AUDIT.md §3.2). Renders a real `<Link>` when *href*
 * is given (navigation gets anchor semantics, not a button faking a
 * link) and a native `<button>` otherwise. Visible focus ring on
 * keyboard focus; disabled state only applies to the button form, since
 * a disabled link isn't a real HTML concept.
 */
export function Button({ variant = 'primary', className, href, ...props }: ButtonProps) {
  const classes = cx(BASE_CLASSES, VARIANT_CLASSES[variant], className)

  if (href !== undefined) {
    return (
      <Link
        href={href}
        className={classes}
        {...(props as AnchorHTMLAttributes<HTMLAnchorElement>)}
      />
    )
  }

  return (
    <button
      className={classes}
      {...(props as ButtonHTMLAttributes<HTMLButtonElement>)}
    />
  )
}
