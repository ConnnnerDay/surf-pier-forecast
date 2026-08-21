import { useId, type InputHTMLAttributes } from 'react'
import { cx } from './cx'

export type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: string
  error?: string
}

/** Labeled input primitive (sprint 27, replacing v2/frontend's `.field`
 * global CSS class -- see R1_RECONCILIATION_AUDIT.md §3.2 and §3.6's
 * accessibility gap). Label, hint, and error are all wired together via
 * `aria-describedby` and `id`, and `aria-invalid` reflects *error* --
 * this is the accessible-primitive contract sprint 27's ledger row
 * names, not just a visual restyle. A generated id via `useId` is used
 * when the caller doesn't supply one, so this is safe to render more
 * than once on a page without id collisions.
 */
export function Field({
  label,
  hint,
  error,
  id,
  className,
  ...props
}: FieldProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const hintId = hint ? `${inputId}-hint` : undefined
  const errorId = error ? `${inputId}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className="mb-4 flex flex-col gap-1.5">
      <label htmlFor={inputId} className="text-sm font-medium text-text">
        {label}
      </label>
      <input
        id={inputId}
        aria-describedby={describedBy}
        aria-invalid={error ? true : undefined}
        className={cx(
          'rounded-md border bg-surface px-3 py-2.5 text-text',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring',
          error ? 'border-nogo-border' : 'border-border',
          className,
        )}
        {...props}
      />
      {hint && (
        <p id={hintId} className="text-sm text-text-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-sm text-nogo-text">
          {error}
        </p>
      )}
    </div>
  )
}
