/** Joins conditional class names, skipping falsy values. A local
 * one-liner instead of a `clsx`/`tailwind-merge` dependency -- this
 * design system's needs don't yet justify the extra package.
 */
export function cx(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ')
}
