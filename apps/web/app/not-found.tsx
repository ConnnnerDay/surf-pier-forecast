import { BrandMark } from './components/brand-mark'
import { Button, Container } from './components/ui'

/** App Router's file-convention 404 page. Logic stays trivial by design
 * -- see R1_RECONCILIATION_AUDIT.md §3.1's disposition for the legacy
 * `NotFound.tsx` this replaces -- but it was the one route left with no
 * Saltline visual identity at all (no mark, no display type, plain
 * left-aligned text) since the rebrand focused on the landing/search/
 * forecast pages. `BrandMark` and centered layout bring it in line with
 * the rest of the site's now-persistent header (app/components/
 * site-header.tsx).
 */
export default function NotFound() {
  return (
    <main>
      <Container>
        <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 py-20 text-center">
          <BrandMark className="h-9 w-9" />
          <h1 className="text-3xl font-bold text-text sm:text-4xl">Page not found</h1>
          <p className="max-w-sm text-text-muted">
            That page doesn&apos;t exist, or the link is out of date.
          </p>
          <Button variant="primary" href="/">
            Back home
          </Button>
        </div>
      </Container>
    </main>
  )
}
