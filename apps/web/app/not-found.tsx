import { Button, Container } from './components/ui'

/** App Router's file-convention 404 page. Trivial by design — see
 * R1_RECONCILIATION_AUDIT.md §3.1's disposition for the legacy
 * `NotFound.tsx` this replaces.
 */
export default function NotFound() {
  return (
    <main>
      <Container>
        <div className="flex flex-col items-start gap-4 py-20">
          <h1 className="text-3xl font-bold text-text">Page not found</h1>
          <p className="text-text-muted">
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
