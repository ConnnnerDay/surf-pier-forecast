import { Badge, type BadgeVariant, Card, Container } from '@/app/components/ui'
import { internalApiFetch } from '@/lib/internal-api-client'

// Forecasts are live, per-request data -- never build-time content. Without
// this, `next build`'s static prerender pass would catch this page's fetch
// failing (apps/api isn't running at build time) and freeze that error into
// the static shell served to every visitor. Explicit over relying on
// no-store's implicit dynamic-opt-out, which a caught fetch error can
// short-circuit before Next's tracking sees it.
export const dynamic = 'force-dynamic'

const DEMO_LOCATION_ID = 'wrightsville-beach-nc'

type ForecastVerdict = 'Excellent' | 'Good' | 'Fair' | 'Challenging' | 'Poor' | 'Unknown'

type ForecastEnvelope = {
  location: { id: string; label: string; timezone: string }
  generated_at: string
  state: string
  confidence: { level: string; reasons: string[] }
  warnings: { code: string; message: string; severity: string }[]
  conditions: {
    score?: { score: number | null; verdict: ForecastVerdict; summary: string } | null
  } | null
}

const VERDICT_TO_BADGE: Record<ForecastVerdict, BadgeVariant> = {
  Excellent: 'go',
  Good: 'go',
  Fair: 'marginal',
  Challenging: 'marginal',
  Poor: 'nogo',
  Unknown: 'neutral',
}

/**
 * Proves ADR-004's signed internal request path end-to-end: this Server
 * Component calls apps/api's real `/v1/forecasts/{id}` through
 * `internalApiFetch` (lib/internal-api-client.ts), which apps/api's
 * `require_internal_signature` dependency now enforces on every `/v1`
 * route. Fixed demo location (no location search yet -- sprint 31) and
 * no auth (no Better Auth session to source a real user from -- sprint
 * 28), so this intentionally isn't the real dashboard (sprint 32) -- the
 * verdict-badge mapping below is demo-page-scoped presentation, not a
 * claim of shipping that sprint.
 *
 * apps/api's own upstream (NOAA/NWS/NDBC) calls are blocked/proxied in
 * this sandboxed environment (docs/R2_CI_BASELINE.md's
 * no-live-provider-dependence rule), so in this sandbox the forecast
 * data itself is expected to render in a degraded state -- that's
 * exactly the "conditions experience... partial/stale/unavailable
 * source-attributed snapshots" story sprint 33 will need, and it's a
 * meaningful proof that degraded responses route and render correctly,
 * not a failure of this page.
 */
export default async function ForecastDemoPage() {
  let forecast: ForecastEnvelope | null = null
  let error: string | null = null

  try {
    forecast = await internalApiFetch<ForecastEnvelope>(
      `/v1/forecasts/${DEMO_LOCATION_ID}`,
    )
  } catch (err) {
    error = err instanceof Error ? err.message : 'Unknown error'
  }

  return (
    <main>
      <Container>
        <header className="py-10 sm:py-16">
          <p className="text-sm font-medium tracking-wide text-primary uppercase">
            ADR-004 signed-request demo — not the real dashboard (sprint 32)
          </p>
          <h1 className="mt-2 text-3xl font-bold text-text sm:text-4xl">
            Live forecast: Wrightsville Beach, NC
          </h1>
          <p className="mt-3 max-w-prose text-text-muted">
            Fetched server-side from apps/api through a signed internal
            request — proves the Next.js BFF and FastAPI boundary works,
            not a finished product screen.
          </p>
        </header>

        <div className="pb-16">
          {error && (
            <Card>
              <h2 className="font-semibold text-text">Couldn&apos;t load the forecast</h2>
              <p className="mt-1 text-sm text-nogo-text">{error}</p>
              <p className="mt-3 text-sm text-text-muted">
                Is apps/api running (<code>uvicorn app.main:app</code>) with a matching
                <code> INTERNAL_SIGNING_KEY_ID</code>/<code>INTERNAL_SIGNING_KEY_SECRET</code>?
                See apps/web/README.md.
              </p>
            </Card>
          )}

          {forecast && (
            <Card className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-xl font-semibold text-text">
                  {forecast.location.label}
                </h2>
                <Badge variant={VERDICT_TO_BADGE[forecast.conditions?.score?.verdict ?? 'Unknown']}>
                  {forecast.conditions?.score?.verdict ?? 'Unknown'}
                </Badge>
              </div>

              {forecast.conditions?.score?.summary && (
                <p className="text-text-muted">{forecast.conditions.score.summary}</p>
              )}

              <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
                <div>
                  <dt className="text-text-muted">State</dt>
                  <dd className="font-medium text-text">{forecast.state}</dd>
                </div>
                <div>
                  <dt className="text-text-muted">Confidence</dt>
                  <dd className="font-medium text-text">{forecast.confidence.level}</dd>
                </div>
                <div>
                  <dt className="text-text-muted">Generated</dt>
                  <dd className="font-medium text-text">{forecast.generated_at}</dd>
                </div>
              </dl>

              {forecast.warnings.length > 0 && (
                <div className="flex flex-col gap-2">
                  <h3 className="text-sm font-medium text-text">Warnings</h3>
                  <ul className="flex flex-col gap-1 text-sm text-text-muted">
                    {forecast.warnings.map((warning) => (
                      <li key={warning.code}>{warning.message}</li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          )}
        </div>
      </Container>
    </main>
  )
}
