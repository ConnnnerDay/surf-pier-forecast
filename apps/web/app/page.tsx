import { Button, Container } from './components/ui'

/**
 * The real landing page, replacing sprint 27's component gallery (moved
 * to `/design-system`) now that the Saltline rebrand gives the app an
 * actual identity to put here. No fabricated verdict badges on the
 * "Popular spots" links below -- showing a plausible-looking Good/
 * Marginal status without a real, current forecast behind it would be
 * exactly the kind of invented data this product's own Integrity rule
 * (docs/product-definition.md) argues against; these are plain links
 * to real curated locations, not a live preview.
 */
export default function Home() {
  return (
    <main>
      <div
        className="ph-photo flex flex-col justify-between px-5 py-6 sm:px-8 sm:py-10"
        style={{ minHeight: '420px' }}
      >
        <div className="relative z-10 flex items-center gap-2 text-white">
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="white"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M2 10 L22 10" />
            <path d="M6 10 A6 6 0 0 1 18 10" />
            <path d="M2 15 Q5 13 8 15 T14 15 T20 15" />
          </svg>
          <span className="font-display text-lg font-bold">Saltline</span>
        </div>

        <div className="relative z-10 flex flex-col gap-4">
          <p className="ph-photo-label">
            Photo placeholder: pier at first light, warm backlight, low horizon
          </p>
          <h1 className="max-w-md text-4xl font-bold leading-[1.05] tracking-tight text-white sm:text-5xl">
            Know before you go.
          </h1>
          <p className="max-w-sm text-base leading-relaxed text-white/85">
            Real tide, wind, and wave data turned into one honest answer —
            go, wait, or skip it.
          </p>
        </div>
      </div>

      <Container>
        <div className="flex flex-col gap-3 py-6">
          <Button variant="primary" href="/locations" className="w-full text-base">
            Find your spot
          </Button>
        </div>

        <section aria-labelledby="value-props-heading" className="py-6">
          <h2 id="value-props-heading" className="sr-only">
            Why Saltline
          </h2>
          <div className="flex flex-col gap-6">
            <div className="flex items-start gap-4">
              <div
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.625rem] bg-go-bg"
                aria-hidden="true"
              >
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="text-go-text" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 7 L12 12 L15.5 14" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-text">One clear verdict</h3>
                <p className="mt-1 text-sm leading-relaxed text-text-muted">
                  Go, marginal, or skip it — with the reasoning shown, not
                  hidden behind a score.
                </p>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <div
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.625rem] bg-nogo-bg"
                aria-hidden="true"
              >
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="text-accent" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 12 Q7 6 11 12 T19 12 T27 12" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-text">Every source shown</h3>
                <p className="mt-1 text-sm leading-relaxed text-text-muted">
                  Marine zone, buoy, tide station — each labeled fresh,
                  degraded, or down. No hidden guesses.
                </p>
              </div>
            </div>

            <div className="flex items-start gap-4">
              <div
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[0.625rem] bg-marginal-bg"
                aria-hidden="true"
              >
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="text-marginal-text" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 20 L4 10 L9 10 L9 4 L15 4 L15 14 L20 14 L20 20 Z" />
                </svg>
              </div>
              <div>
                <h3 className="text-base font-bold text-text">
                  The best hour, not just today
                </h3>
                <p className="mt-1 text-sm leading-relaxed text-text-muted">
                  Tide swings and solunar windows narrowed down to when it&apos;s
                  actually worth showing up.
                </p>
              </div>
            </div>
          </div>
        </section>

        <section aria-labelledby="popular-spots-heading" className="flex flex-col gap-3 pb-16">
          <h2
            id="popular-spots-heading"
            className="text-xs font-bold uppercase tracking-wide text-text-muted"
          >
            Popular spots
          </h2>
          <div className="flex flex-col gap-2.5">
            <a
              href="/forecast/wrightsville-beach-nc"
              className="flex items-center justify-between rounded-[0.875rem] border border-border bg-surface px-4 py-3.5 text-text no-underline transition-colors hover:bg-bg"
            >
              <span className="font-semibold">Wrightsville Beach, NC</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="text-text-muted" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 6 L15 12 L9 18" />
              </svg>
            </a>
            <a
              href="/forecast/cocoa-beach-fl"
              className="flex items-center justify-between rounded-[0.875rem] border border-border bg-surface px-4 py-3.5 text-text no-underline transition-colors hover:bg-bg"
            >
              <span className="font-semibold">Cocoa Beach / Port Canaveral, FL</span>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" className="text-text-muted" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M9 6 L15 12 L9 18" />
              </svg>
            </a>
          </div>
        </section>
      </Container>
    </main>
  )
}
