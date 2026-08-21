import { notFound } from 'next/navigation'
import { Container } from '@/app/components/ui'
import { ForecastCard, ForecastErrorCard, type ForecastEnvelope } from '@/app/components/forecast-card'
import { internalApiFetch, InternalApiError } from '@/lib/internal-api-client'

// Forecasts are live, per-request data -- never build-time content. See
// app/forecast/[locationId]/page.tsx's git history / the roadmap
// checkpoint for why this matters with Next.js 16's static prerendering.
export const dynamic = 'force-dynamic'

/**
 * The real per-location forecast lookup (replaces the earlier fixed
 * `/forecast/demo` proof page -- same signed-request path, but for any
 * `location_id` apps/api recognizes, not just Wrightsville Beach).
 * Reached from app/locations/page.tsx's search results. A 404 from
 * apps/api (unknown location_id) becomes this page's own not-found
 * state via `notFound()`, distinct from a generic fetch failure.
 */
export default async function ForecastPage({
  params,
}: {
  params: Promise<{ locationId: string }>
}) {
  const { locationId } = await params

  let forecast: ForecastEnvelope | null = null
  let error: string | null = null

  try {
    forecast = await internalApiFetch<ForecastEnvelope>(
      `/v1/forecasts/${encodeURIComponent(locationId)}`,
    )
  } catch (err) {
    if (err instanceof InternalApiError && err.status === 404) {
      notFound()
    }
    error = err instanceof Error ? err.message : 'Unknown error'
  }

  return (
    <main>
      <Container>
        <header className="py-10 sm:py-16">
          <p className="text-sm font-medium tracking-wide text-primary uppercase">
            Signed-request forecast lookup — not the real dashboard (sprint 32)
          </p>
          <h1 className="mt-2 text-3xl font-bold text-text sm:text-4xl">
            {forecast?.location.label ?? 'Forecast'}
          </h1>
          <p className="mt-3 max-w-prose text-text-muted">
            Fetched server-side from apps/api through a signed internal
            request for a real, searched location — not a fixed demo.
          </p>
        </header>

        <div className="pb-16">
          {error && <ForecastErrorCard message={error} />}
          {forecast && <ForecastCard forecast={forecast} />}
        </div>
      </Container>
    </main>
  )
}
