import { cache } from 'react'
import type { Metadata } from 'next'
import Link from 'next/link'
import { notFound } from 'next/navigation'
import { Container } from '@/app/components/ui'
import { ForecastCard, ForecastErrorCard, type ForecastEnvelope } from '@/app/components/forecast-card'
import { internalApiFetch, InternalApiError } from '@/lib/internal-api-client'

// Forecasts are live, per-request data -- never build-time content. See
// app/forecast/[locationId]/page.tsx's git history / the roadmap
// checkpoint for why this matters with Next.js 16's static prerendering.
export const dynamic = 'force-dynamic'

type Props = { params: Promise<{ locationId: string }> }

/**
 * Wrapped in React's `cache()`, which was intended to make
 * `generateMetadata` and the page body share one real network round
 * trip per request instead of two (`fetch`'s own automatic per-request
 * memoization can't help here regardless: ADR-004 signs every request
 * with a fresh `requestId`/timestamp, so two calls for the same
 * location never look like the same `fetch(...)` call to that
 * mechanism). **Verified not to work as intended, kept anyway because
 * it's harmless**: a real two-server trace (sprint 41's
 * `app.infra.request_logging`, checked by grepping the same
 * `request_id` across both services' logs for one page load) showed
 * two distinct signed calls reaching apps/api for a single page view,
 * not one -- `generateMetadata` and this component's own call are not
 * being deduped by `cache()` in this Next.js/Turbopack setup, for a
 * reason not root-caused here (candidates: `force-dynamic` rendering,
 * or `generateMetadata` resolving in a separate pass with its own
 * request-scoped cache). The real-world cost stays low regardless:
 * `apps/api`'s own sprint-24 `SnapshotCache` (a 4-hour freshness
 * window) absorbs the second call almost for free (single-digit
 * milliseconds in that same trace, versus several seconds for the
 * first, cold call) -- so this is a known, measured inefficiency, not
 * an invented "it works" claim, and not worth a deeper fix without
 * more evidence it matters in practice.
 */
const getForecast = cache((locationId: string) =>
  internalApiFetch<ForecastEnvelope>(`/v1/forecasts/${encodeURIComponent(locationId)}`),
)

/**
 * Sprint 49's "public non-personal forecast pages (organic-growth
 * surface)": real per-location title/description/canonical/OpenGraph
 * metadata, not the root layout's generic default. The description is
 * deliberately static copy about the location, never live score/warning
 * text -- a degraded-conditions sentence (e.g. a real "could not
 * connect to..." warning) has no business being cached into a search
 * result or link-preview card. A 404 (unknown `location_id`) calls
 * `notFound()` here too, matching the page body below, so a bad link
 * gets Next's real not-found metadata instead of a fabricated title.
 * Any other fetch failure falls back to generic metadata rather than
 * throwing -- a metadata error shouldn't take down a page whose body
 * would otherwise render its own graceful `ForecastErrorCard`.
 */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { locationId } = await params

  let forecast: ForecastEnvelope | null = null
  try {
    forecast = await getForecast(locationId)
  } catch (err) {
    if (err instanceof InternalApiError && err.status === 404) {
      notFound()
    }
    return { title: 'Forecast' }
  }

  const title = `${forecast.location.label} Fishing Forecast`
  const description = `Surf and pier fishing conditions for ${forecast.location.label}: wind, waves, water temperature, tides, and the best times to fish today.`
  const canonicalPath = `/forecast/${encodeURIComponent(locationId)}`

  return {
    title,
    description,
    alternates: { canonical: canonicalPath },
    openGraph: { title, description, url: canonicalPath },
  }
}

/**
 * The real per-location forecast lookup (replaces the earlier fixed
 * `/forecast/demo` proof page -- same signed-request path, but for any
 * `location_id` apps/api recognizes, not just Wrightsville Beach).
 * Reached from app/locations/page.tsx's search results. A 404 from
 * apps/api (unknown location_id) becomes this page's own not-found
 * state via `notFound()`, distinct from a generic fetch failure.
 */
export default async function ForecastPage({ params }: Props) {
  const { locationId } = await params

  let forecast: ForecastEnvelope | null = null
  let error: string | null = null

  try {
    forecast = await getForecast(locationId)
  } catch (err) {
    if (err instanceof InternalApiError && err.status === 404) {
      notFound()
    }
    error = err instanceof Error ? err.message : 'Unknown error'
  }

  return (
    <main>
      <div className="ph-photo flex flex-col justify-between px-5 py-5" style={{ minHeight: '190px' }}>
        <Link
          href="/locations"
          className="relative z-10 flex h-9 w-9 items-center justify-center rounded-[0.625rem] bg-white/20 text-white no-underline"
          aria-label="Back to search"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M15 5 L8 12 L15 19" />
          </svg>
        </Link>
        <div className="relative z-10 flex flex-col gap-1.5">
          <p className="ph-photo-label">Photo placeholder: pier pilings at low tide, wide shot</p>
          <h1 className="text-2xl font-bold text-white sm:text-3xl">
            {forecast?.location.label ?? 'Forecast'}
          </h1>
        </div>
      </div>

      <Container>
        <div className="py-6 pb-16">
          {error && <ForecastErrorCard message={error} />}
          {forecast && <ForecastCard forecast={forecast} />}
        </div>
      </Container>
    </main>
  )
}
