import { Badge, type BadgeVariant, Card } from './ui'

export type ForecastVerdict = 'Excellent' | 'Good' | 'Fair' | 'Challenging' | 'Poor' | 'Unknown'

export type TidePrediction = {
  time: string
  kind: 'high' | 'low'
  height_ft: number
}

export type ForecastTides = {
  station_id: string
  predictions: TidePrediction[]
}

export type SourceStatus = {
  provider: string
  state: 'ok' | 'degraded' | 'unavailable'
  as_of: string
  detail?: string | null
}

export type ForecastEnvelope = {
  location: { id: string; label: string; timezone: string }
  generated_at: string
  state: 'fresh' | 'stale' | 'partial' | 'unavailable'
  sources: SourceStatus[]
  confidence: { level: string; reasons: string[] }
  warnings: { code: string; message: string; severity: string }[]
  conditions: {
    score?: { score: number | null; verdict: ForecastVerdict; summary: string } | null
  } | null
  tides: ForecastTides | null
}

const VERDICT_TO_BADGE: Record<ForecastVerdict, BadgeVariant> = {
  Excellent: 'go',
  Good: 'go',
  Fair: 'marginal',
  Challenging: 'marginal',
  Poor: 'nogo',
  Unknown: 'neutral',
}

const FORECAST_STATE_TO_BADGE: Record<ForecastEnvelope['state'], BadgeVariant> = {
  fresh: 'go',
  stale: 'marginal',
  partial: 'marginal',
  unavailable: 'nogo',
}

const SOURCE_STATE_TO_BADGE: Record<SourceStatus['state'], BadgeVariant> = {
  ok: 'go',
  degraded: 'marginal',
  unavailable: 'nogo',
}

const PROVIDER_LABELS: Record<string, string> = {
  'nws:marine_zone': 'NWS marine zone',
  'noaa_coops:water_temperature': 'Water temperature',
  'ndbc:buoy': 'NDBC buoy',
  'noaa_coops:tides': 'Tide predictions',
  'noaa_coops:wind': 'CO-OPS wind (fallback)',
  'nws:gridpoint_wind': 'NWS gridpoint wind (fallback)',
}

function providerLabel(provider: string): string {
  return (
    PROVIDER_LABELS[provider] ??
    provider
      .replace(/[_:]/g, ' ')
      .replace(/\b\w/g, (char) => char.toUpperCase())
  )
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

/**
 * Sprint 33's "full/partial/stale/unavailable source-attributed
 * snapshots" -- each source apps/api fanned out to (marine zone, water
 * temperature, buoy, tides, and the wind-fallback chain when it fires)
 * shown individually, not just folded into the aggregate `state`/
 * `warnings` already rendered above. `detail` (a raw provider error
 * string) is shown only for non-ok sources, since it's the honest
 * "why" behind a degraded/unavailable badge.
 */
function SourceStatusList({ sources }: { sources: SourceStatus[] }) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium text-text">Data sources</h3>
      <ul className="flex flex-col gap-2">
        {sources.map((source) => (
          <li key={source.provider} className="flex flex-col gap-1 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-text">{providerLabel(source.provider)}</span>
              <Badge variant={SOURCE_STATE_TO_BADGE[source.state]}>
                {capitalize(source.state)}
              </Badge>
            </div>
            {source.state !== 'ok' && source.detail && (
              <p className="break-words text-text-muted">{source.detail}</p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * Sprint 34's tide table -- the text-alternative half of that sprint's
 * "accessible charts, text alternatives" acceptance bar. A visual
 * chart is deliberately not attempted here; a plain, properly-labeled
 * `<table>` is itself an accessible representation, not a fallback for
 * one. Times are formatted in the *location's* timezone
 * (`forecast.location.timezone`), not the viewer's browser timezone --
 * a tide time is only meaningful relative to the place it's for.
 */
function TideTable({
  tides,
  timezone,
}: {
  tides: ForecastTides
  timezone: string
}) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
  })

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium text-text">Upcoming tides</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">
            Upcoming high and low tides for {tides.station_id}
          </caption>
          <thead>
            <tr className="text-text-muted">
              <th scope="col" className="py-1 pr-4 font-medium">
                Time
              </th>
              <th scope="col" className="py-1 pr-4 font-medium">
                Tide
              </th>
              <th scope="col" className="py-1 font-medium">
                Height
              </th>
            </tr>
          </thead>
          <tbody>
            {tides.predictions.map((prediction) => (
              <tr key={prediction.time} className="border-t border-border">
                <td className="py-1.5 pr-4 text-text">
                  {formatter.format(new Date(prediction.time))}
                </td>
                <td className="py-1.5 pr-4 text-text">
                  {prediction.kind === 'high' ? 'High' : 'Low'}
                </td>
                <td className="py-1.5 text-text">{prediction.height_ft.toFixed(1)} ft</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

/**
 * Presentational rendering of a `ForecastEnvelope` -- shared by
 * app/forecast/[locationId]/page.tsx (the real per-location lookup) so
 * there's one place this shape gets rendered, not one per page. Not the
 * real dashboard (sprint 32): the verdict-badge mapping here is
 * page-scoped presentation, not that sprint's product hierarchy.
 *
 * The `State` badge and `SourceStatusList` below are sprint 33's
 * "full/partial/stale/unavailable source-attributed snapshots" --
 * apps/api's `ForecastState`/`SourceState` vocabulary rendered
 * directly, not reinterpreted.
 */
export function ForecastCard({ forecast }: { forecast: ForecastEnvelope }) {
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-text">{forecast.location.label}</h2>
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
          <dd className="mt-1">
            <Badge variant={FORECAST_STATE_TO_BADGE[forecast.state]}>
              {capitalize(forecast.state)}
            </Badge>
          </dd>
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

      {forecast.sources.length > 0 && <SourceStatusList sources={forecast.sources} />}

      {forecast.tides && (
        <TideTable tides={forecast.tides} timezone={forecast.location.timezone} />
      )}

      {forecast.warnings.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-sm font-medium text-text">Warnings</h3>
          <ul className="flex flex-col gap-1 text-sm text-text-muted">
            {forecast.warnings.map((warning) => (
              <li key={warning.code} className="break-words">
                {warning.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  )
}

/** The "couldn't load" state, shared the same way. */
export function ForecastErrorCard({ message }: { message: string }) {
  return (
    <Card>
      <h2 className="font-semibold text-text">Couldn&apos;t load the forecast</h2>
      <p className="mt-1 break-words text-sm text-danger-text">{message}</p>
      <p className="mt-3 text-sm text-text-muted">
        Is apps/api running (<code>uvicorn app.main:app</code>) with a matching{' '}
        <code>INTERNAL_SIGNING_KEY_ID</code>/<code>INTERNAL_SIGNING_KEY_SECRET</code>? See
        apps/web/README.md.
      </p>
    </Card>
  )
}
