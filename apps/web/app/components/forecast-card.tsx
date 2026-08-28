import { Badge, type BadgeVariant, Card } from './ui'
import { cx } from './ui/cx'

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

export type ActivityTag = 'low' | 'med' | 'high' | 'prime'

export type HourlyActivity = {
  hour: number
  time: string
  level: number
  tag: ActivityTag
  is_now: boolean
  peak: boolean
  reasons: string[]
  sun_event: 'sunrise' | 'sunset' | null
  tide_event: 'high' | 'low' | null
  tide_time: string | null
  feeding: 'major' | 'minor' | null
}

export type HourlyOutlook = {
  hours: HourlyActivity[]
}

export type Observation = {
  value: number
  unit: string
  is_fallback: boolean
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
    water_temperature: Observation
    wind_range_kt: [number, number] | null
    wave_range_ft: [number, number] | null
    wind_direction: string | null
  } | null
  tides: ForecastTides | null
  hourly_outlook: HourlyOutlook | null
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

const ACTIVITY_TAG_TO_BADGE: Record<ActivityTag, BadgeVariant> = {
  prime: 'go',
  high: 'go',
  med: 'marginal',
  low: 'neutral',
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

const _TIDE_CHART_WIDTH = 600
const _TIDE_CHART_HEIGHT = 110
const _TIDE_CHART_TOP = 16
const _TIDE_CHART_BOTTOM = 84
// Horizontal padding so the first/last point's centered height label
// (text-anchor="middle") isn't clipped by the viewBox edge.
const _TIDE_CHART_SIDE_PADDING = 18

/**
 * Sprint 34's remaining "accessible charts" sub-item for tides -- a
 * point chart, not an interpolated curve: NOAA CO-OPS's `hilo`
 * predictions product (`app.providers.noaa_coops.fetch_tide_predictions`)
 * gives real predicted heights only at each high/low extremum, not a
 * continuous hourly series, so plotting a smooth cosine-shaped curve
 * between them would be *inventing* the in-between shape rather than
 * showing real predicted values -- exactly the kind of unsupported
 * invention `docs/product-definition.md`'s Integrity rule (already
 * enforced server-side, e.g. `is_fallback` labeling) argues against
 * doing on the frontend too. Straight lines between real points are
 * an honest visual summary of the same handful of values
 * `TideTable` already lists as text; every point still gets a native
 * SVG `<title>` with its exact time/height, and each point's height
 * is labeled directly (a small point count, unlike the 24-bar hourly
 * chart, is exactly the case `dataviz`'s "label selectively" guidance
 * allows labeling every point). Single-hue on `--color-primary` (a
 * magnitude series, not a categorical one -- same reasoning as
 * `HourlyOutlookChart`), `aria-hidden="true"` alongside the
 * already-complete `TideTable`, no `tabindex` inside it (an
 * `aria-hidden` subtree must never contain a keyboard-focusable
 * element). The x-axis is real elapsed time between the first and
 * last prediction, not evenly-spaced-by-index, since predictions can
 * span more than one day unevenly.
 */
function TideChart({ tides, timezone }: { tides: ForecastTides; timezone: string }) {
  const points = tides.predictions
  if (points.length < 2) return null

  const times = points.map((p) => new Date(p.time).getTime())
  const heights = points.map((p) => p.height_ft)
  const minTime = Math.min(...times)
  const maxTime = Math.max(...times)
  const minHeight = Math.min(...heights)
  const maxHeight = Math.max(...heights)
  const timeSpan = maxTime - minTime || 1
  const heightSpan = maxHeight - minHeight || 1

  const timeFormatter = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
  })

  const plotWidth = _TIDE_CHART_WIDTH - _TIDE_CHART_SIDE_PADDING * 2
  const coords = points.map((prediction, index) => ({
    prediction,
    x: _TIDE_CHART_SIDE_PADDING + ((times[index] - minTime) / timeSpan) * plotWidth,
    y:
      _TIDE_CHART_BOTTOM -
      ((prediction.height_ft - minHeight) / heightSpan) *
        (_TIDE_CHART_BOTTOM - _TIDE_CHART_TOP),
  }))

  const linePath = coords.map((c, i) => `${i === 0 ? 'M' : 'L'} ${c.x} ${c.y}`).join(' ')

  return (
    <div aria-hidden="true" className="flex flex-col gap-1">
      <svg
        viewBox={`0 0 ${_TIDE_CHART_WIDTH} ${_TIDE_CHART_HEIGHT}`}
        role="presentation"
        className="w-full"
      >
        <path d={linePath} fill="none" strokeWidth={2} className="stroke-primary" />
        {coords.map(({ prediction, x, y }) => (
          <g key={prediction.time}>
            <circle cx={x} cy={y} r={4} className="fill-primary">
              <title>
                {`${prediction.kind === 'high' ? 'High' : 'Low'} tide, ${timeFormatter.format(
                  new Date(prediction.time),
                )}: ${prediction.height_ft.toFixed(1)} ft`}
              </title>
            </circle>
            <text
              x={x}
              y={prediction.kind === 'high' ? y - 10 : y + 18}
              textAnchor="middle"
              className="fill-text-muted text-[9px]"
            >
              {prediction.height_ft.toFixed(1)}
            </text>
          </g>
        ))}
      </svg>
      <p className="text-xs text-text-muted">
        Predicted tide height over time; each point is a high or low tide.
      </p>
    </div>
  )
}

/**
 * Sprint 34's tide table -- the text-alternative half of that sprint's
 * "accessible charts, text alternatives" acceptance bar, rendered
 * alongside `TideChart` above (the real accessible representation --
 * the chart is `aria-hidden`). A plain, properly-labeled `<table>` is
 * itself an accessible representation, not a fallback for one. Times
 * are formatted in the *location's* timezone
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

const _SHORT_HOURS = [
  '12a', '1a', '2a', '3a', '4a', '5a', '6a', '7a', '8a', '9a', '10a', '11a',
  '12p', '1p', '2p', '3p', '4p', '5p', '6p', '7p', '8p', '9p', '10p', '11p',
]

const _CHART_SLOT = 30
const _CHART_BAR_WIDTH = 22
const _CHART_BAR_MAX_HEIGHT = 90
const _CHART_BASELINE_Y = 100
const _CHART_WIDTH = _CHART_SLOT * 24
const _CHART_HEIGHT = 128

/**
 * Sprint 34's hourly-outlook visual chart -- the "accessible charts"
 * half of the acceptance bar, paired with `HourlyOutlookTable`'s
 * already-complete text-alternative half. This is deliberately *not*
 * the accessible representation itself: `aria-hidden="true"` removes
 * it from the accessibility tree entirely, since `HourlyOutlookTable`
 * (rendered alongside it, not replaced by it) already carries the same
 * data as real text -- a chart re-announcing a table a screen-reader
 * user just read is noise, not a service. Per the design system's
 * `dataviz` skill: this is a single-metric magnitude series (one
 * activity level per hour, not a categorical identity to color-key),
 * so it gets a single-hue sequential encoding -- `--color-primary`
 * at variable opacity (bar height *and* opacity both track `level`,
 * a deliberately redundant pair of channels since this chart carries
 * no information the table doesn't already have) -- rather than
 * inventing new discrete color tokens for `ActivityTag`'s four tiers.
 * `--color-go-text`/`--color-marginal-text` (used elsewhere for
 * *paired* badge pills) were considered and rejected for a bar fill:
 * per `app/globals.css`'s own comment on `--color-danger-text`, those
 * tokens are theme-invariant, tuned only for pairing with their own
 * light-tint badge background -- not for a solid fill against the
 * card surface, where they'd risk the exact dark-mode contrast bug
 * the sprint-27 `axe-core` pass already found and fixed once for
 * `--color-nogo-text`. `--color-primary`/`--color-accent` are already
 * theme-aware (separate light/dark values), so reusing them sidesteps
 * that risk entirely instead of adding new tokens to re-solve it.
 * The current hour gets an accent-colored ring; the day's peak
 * hour(s) get an accent dot. Each bar carries a native SVG `<title>`
 * (mouse-hover tooltip) but no `tabindex`/focus handling -- an
 * `aria-hidden` subtree must never contain a keyboard-focusable
 * element (axe-core's `aria-hidden-focus` rule), and every value a
 * hover tooltip could show is already a keyboard/screen-reader-
 * reachable table cell one section down.
 */
function HourlyOutlookChart({ outlook }: { outlook: HourlyOutlook }) {
  return (
    <div aria-hidden="true" className="flex flex-col gap-1">
      <svg
        viewBox={`0 0 ${_CHART_WIDTH} ${_CHART_HEIGHT}`}
        role="presentation"
        className="w-full"
      >
        <line
          x1={0}
          y1={_CHART_BASELINE_Y}
          x2={_CHART_WIDTH}
          y2={_CHART_BASELINE_Y}
          strokeWidth={1}
          className="stroke-border"
        />
        {outlook.hours.map((hour, index) => {
          const x = index * _CHART_SLOT + (_CHART_SLOT - _CHART_BAR_WIDTH) / 2
          const barHeight = Math.max(2, (hour.level / 100) * _CHART_BAR_MAX_HEIGHT)
          const y = _CHART_BASELINE_Y - barHeight
          return (
            <g key={hour.hour}>
              <rect
                x={x}
                y={y}
                width={_CHART_BAR_WIDTH}
                height={barHeight}
                rx={3}
                strokeWidth={hour.is_now ? 2 : 0}
                className={cx('fill-primary', hour.is_now && 'stroke-accent')}
                style={{ opacity: 0.25 + 0.75 * (hour.level / 100) }}
              >
                <title>{`${_SHORT_HOURS[hour.hour]}: ${hour.level}/100 (${hour.tag})`}</title>
              </rect>
              {hour.peak && (
                <circle
                  cx={x + _CHART_BAR_WIDTH / 2}
                  cy={y - 6}
                  r={3}
                  className="fill-accent"
                />
              )}
            </g>
          )
        })}
        {outlook.hours.map((hour, index) =>
          index % 3 === 0 ? (
            <text
              key={`label-${hour.hour}`}
              x={index * _CHART_SLOT + _CHART_SLOT / 2}
              y={_CHART_HEIGHT - 6}
              textAnchor="middle"
              className="fill-text-muted text-[9px]"
            >
              {_SHORT_HOURS[hour.hour]}
            </text>
          ) : null,
        )}
      </svg>
      <p className="text-xs text-text-muted">
        Taller, brighter bars mean better estimated activity; the dot marks the
        day&apos;s peak.
      </p>
    </div>
  )
}

/**
 * Sprint 34's hourly outlook table -- the "text alternatives" half of
 * the acceptance bar (apps/api's `app.domain.timing.
 * build_hourly_outlook`, backend), rendered alongside
 * `HourlyOutlookChart` above as the real accessible representation
 * (the chart is `aria-hidden`). A plain, properly-labeled `<table>` is
 * itself an accessible representation, not a fallback for one.
 * `hour.reasons`/`tide_event` are apps/api's own plain-language "why"
 * for that hour's estimate, shown verbatim rather than re-derived here.
 */
function HourlyOutlookTable({
  outlook,
  timezone,
}: {
  outlook: HourlyOutlook
  timezone: string
}) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    hour: 'numeric',
    minute: '2-digit',
  })

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium text-text">Hourly activity outlook</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">
            Estimated hourly fish-activity level, today, in {timezone}
          </caption>
          <thead>
            <tr className="text-text-muted">
              <th scope="col" className="py-1 pr-4 font-medium">
                Time
              </th>
              <th scope="col" className="py-1 pr-4 font-medium">
                Activity
              </th>
              <th scope="col" className="py-1 font-medium">
                Why
              </th>
            </tr>
          </thead>
          <tbody>
            {outlook.hours.map((hour) => {
              const why = [
                ...hour.reasons,
                hour.tide_event ? `${capitalize(hour.tide_event)} tide` : null,
              ]
                .filter((part): part is string => Boolean(part))
                .join(' · ')
              return (
                <tr
                  key={hour.hour}
                  className={cx('border-t border-border', hour.is_now && 'bg-primary/10')}
                >
                  <td className="py-1.5 pr-4 whitespace-nowrap text-text">
                    {formatter.format(new Date(hour.time))}
                    {hour.is_now && <span className="text-text-muted"> (now)</span>}
                  </td>
                  <td className="py-1.5 pr-4">
                    <Badge variant={ACTIVITY_TAG_TO_BADGE[hour.tag]}>
                      {capitalize(hour.tag)}
                      {hour.peak && ' · Peak'}
                    </Badge>
                  </td>
                  <td className="py-1.5 break-words text-text-muted">{why || '—'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const TAG_RANK: Record<ActivityTag, number> = { low: 0, med: 1, high: 2, prime: 3 }

export type BestWindow = { hours: HourlyActivity[]; tag: ActivityTag }

/**
 * Sprint 32's "best window" -- derived entirely from `hourly_outlook`
 * already on hand, no extra fetch, per `app.domain.timing`'s own
 * docstring flagging this as the natural follow-up. The longest
 * contiguous run of hours at the day's best activity tag (ties broken
 * by whichever run comes first); `null` when the day never reaches
 * `high` (a `low`/`med`-only day has no window worth calling out).
 */
export function deriveBestWindow(outlook: HourlyOutlook): BestWindow | null {
  const bestRank = Math.max(...outlook.hours.map((hour) => TAG_RANK[hour.tag]))
  if (bestRank < TAG_RANK.high) return null
  const bestTag = (Object.keys(TAG_RANK) as ActivityTag[]).find(
    (tag) => TAG_RANK[tag] === bestRank,
  )!

  let bestRun: HourlyActivity[] = []
  let currentRun: HourlyActivity[] = []
  for (const hour of outlook.hours) {
    if (hour.tag === bestTag) {
      currentRun = [...currentRun, hour]
      if (currentRun.length > bestRun.length) bestRun = currentRun
    } else {
      currentRun = []
    }
  }
  return bestRun.length > 0 ? { hours: bestRun, tag: bestTag } : null
}

/**
 * Sprint 32's "conditions" summary -- the wind/wave/water-temperature
 * line the acceptance bar names between "best window" and
 * "confidence, freshness." Shows `app.domain.assembly`'s own
 * already-reconciled `wind_range_kt`/`wave_range_ft`/`wind_direction`
 * (the exact numbers `score_conditions` was computed from) verbatim,
 * rather than re-deriving that NWS-marine-zone-over-NDBC-buoy
 * source-preference policy from the per-source fields on the frontend
 * a second time.
 */
function ConditionsSummary({
  conditions,
}: {
  conditions: NonNullable<ForecastEnvelope['conditions']>
}) {
  const parts: string[] = []
  if (conditions.wind_range_kt) {
    const [low, high] = conditions.wind_range_kt
    const range = low === high ? `${low}` : `${low}–${high}`
    parts.push(`Wind ${range} kt${conditions.wind_direction ? ` ${conditions.wind_direction}` : ''}`)
  }
  if (conditions.wave_range_ft) {
    const [low, high] = conditions.wave_range_ft
    const range = low === high ? `${low}` : `${low}–${high}`
    parts.push(`Waves ${range} ft`)
  }
  const waterTemp = conditions.water_temperature
  parts.push(
    `Water ${Math.round(waterTemp.value)}°F${waterTemp.is_fallback ? ' (monthly avg)' : ''}`,
  )

  return <p className="text-sm text-text">{parts.join(' · ')}</p>
}

function BestWindowCallout({ window, timezone }: { window: BestWindow; timezone: string }) {
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    hour: 'numeric',
    minute: '2-digit',
  })
  const start = window.hours[0]
  const end = window.hours[window.hours.length - 1]
  const rangeLabel =
    start.hour === end.hour
      ? formatter.format(new Date(start.time))
      : `${formatter.format(new Date(start.time))} – ${formatter.format(new Date(end.time))}`

  return (
    <p className="text-sm text-text-muted">
      Best window <span className="font-medium text-text">{rangeLabel}</span>
      {' · '}
      <Badge variant={ACTIVITY_TAG_TO_BADGE[window.tag]}>{capitalize(window.tag)}</Badge>
    </p>
  )
}

/**
 * Presentational rendering of a `ForecastEnvelope` -- shared by
 * app/forecast/[locationId]/page.tsx (the real per-location lookup) so
 * there's one place this shape gets rendered, not one per page.
 *
 * Sprint 32's "go/no-go as a simple traffic-light headline (score/
 * narrative expandable, not primary); best window, conditions,
 * confidence, freshness first": the verdict `Badge` is the primary
 * headline (enlarged, first), `deriveBestWindow`'s callout sits right
 * beneath it, `ConditionsSummary` renders the wind/wave/water-temperature
 * line next, and the numeric score plus its plain-language narrative
 * move into a `<details>` -- present, but demoted, not the first thing
 * a reader sees. Confidence/state/freshness stay in the summary strip
 * below. This is not the real multi-location dashboard (still sprint
 * 37's job): the verdict-badge mapping here is page-scoped
 * presentation, not that sprint's full product hierarchy.
 * `ConditionsSummary` shows apps/api's own already-reconciled
 * `wind_range_kt`/`wave_range_ft`/`wind_direction` (added to
 * `ForecastConditions` post-sprint-32 for exactly this) verbatim,
 * rather than re-deriving `app.domain.assembly`'s NWS-marine-zone-
 * over-NDBC-buoy source-preference policy from the per-source fields
 * on the frontend a second time.
 *
 * The `State` badge and `SourceStatusList` below are sprint 33's
 * "full/partial/stale/unavailable source-attributed snapshots" --
 * apps/api's `ForecastState`/`SourceState` vocabulary rendered
 * directly, not reinterpreted.
 */
export function ForecastCard({ forecast }: { forecast: ForecastEnvelope }) {
  const score = forecast.conditions?.score
  const bestWindow = forecast.hourly_outlook ? deriveBestWindow(forecast.hourly_outlook) : null

  return (
    <Card className="flex flex-col gap-4">
      <h2 className="text-sm font-medium text-text-muted">{forecast.location.label}</h2>

      <div className="flex flex-col gap-2">
        <Badge
          variant={VERDICT_TO_BADGE[score?.verdict ?? 'Unknown']}
          className="w-fit px-4 py-2 text-base"
        >
          {score?.verdict ?? 'Unknown'}
        </Badge>
        {bestWindow && (
          <BestWindowCallout window={bestWindow} timezone={forecast.location.timezone} />
        )}
      </div>

      {forecast.conditions && <ConditionsSummary conditions={forecast.conditions} />}

      {score?.summary && (
        <details className="text-sm">
          <summary className="cursor-pointer text-text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus-ring">
            Score details{score.score !== null ? ` (${score.score}/100)` : ''}
          </summary>
          <p className="mt-2 text-text-muted">{score.summary}</p>
        </details>
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
        <>
          <TideChart tides={forecast.tides} timezone={forecast.location.timezone} />
          <TideTable tides={forecast.tides} timezone={forecast.location.timezone} />
        </>
      )}

      {forecast.hourly_outlook && (
        <>
          <HourlyOutlookChart outlook={forecast.hourly_outlook} />
          <HourlyOutlookTable
            outlook={forecast.hourly_outlook}
            timezone={forecast.location.timezone}
          />
        </>
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
