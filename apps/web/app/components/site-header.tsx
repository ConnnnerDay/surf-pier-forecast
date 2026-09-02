import Link from 'next/link'
import { BrandMark } from './brand-mark'
import { Container } from './ui'

/**
 * The one piece of the Saltline rebrand every other page was still
 * missing: a persistent header. Sprint 27's rework gave the landing
 * page (app/page.tsx), the forecast page, and the search page each
 * their own bespoke layout, but none of them -- nor app/not-found.tsx,
 * nor app/design-system/page.tsx -- shared any wayfinding back to the
 * brand or to search. Rendered once from app/layout.tsx so every route
 * gets it for free, rather than re-added per page.
 */
export function SiteHeader() {
  return (
    <header className="border-b border-border bg-surface">
      <Container>
        <div className="flex h-16 items-center justify-between">
          <Link
            href="/"
            aria-label="Saltline home"
            className="flex items-center gap-2 text-text no-underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface rounded-sm"
          >
            <BrandMark />
            <span className="font-display text-lg font-bold">Saltline</span>
          </Link>
          <Link
            href="/locations"
            className="text-sm font-semibold text-primary no-underline hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring focus-visible:ring-offset-2 focus-visible:ring-offset-surface rounded-sm"
          >
            Find your spot
          </Link>
        </div>
      </Container>
    </header>
  )
}
