> **Status: accepted as sprint 2 evidence** (see `docs/CANONICAL_ROADMAP.md`'s
> sprint ledger). Recovered verbatim from closed PR #310, which predates the
> canonical roadmap (R0) and was never merged. Content below is unchanged from
> that PR. Per the roadmap's source-of-truth order, the "Product decisions on
> record" sections in `docs/CANONICAL_ROADMAP.md` — recorded later, directly
> by the product owner — refine and take priority over anything below where
> the two overlap (e.g. the traffic-light go/no-go presentation, "Surfline for
> fishing" positioning, the saved-location-count paid-tier mechanism, i18n
> readiness, and CAPTCHA/bot-defense requirements were decided after this
> document and aren't reflected in it). No direct contradiction was found —
> those are refinements, not reversals.

# Sprint 2: Product definition

Status: proposed v1 contract

Audience: product, design, engineering, operations, and AI reviewers

## Product promise

Surf & Pier Forecast helps a US coastal angler answer three questions from a
phone:

1. Is it reasonable and safe for me to fish this location?
2. When is the most promising practical window?
3. Which observed or forecast conditions support that answer?

The product combines public marine and weather sources into a concise outlook.
It does not claim to predict catches. Every conclusion must remain traceable to
available evidence, and incomplete evidence must remain visibly incomplete.

## Primary user

The v1 user is a recreational US surf or pier angler who:

- usually checks conditions shortly before deciding whether to make a trip;
- primarily uses a phone, often outdoors and in poor connectivity;
- understands common fishing terms but should not need meteorological expertise;
- wants a quick answer before exploring detailed readings;
- may choose any valid US coastal point, not only a curated destination; and
- is willing to create an account to retain preferences and saved locations.

Fishing guides, offshore boat anglers, researchers, regulators, and users
outside supported US coastal regions are not primary v1 audiences.

## Core user journey

### First visit

1. The visitor sees a concise product explanation and creates an account with
   email and password.
2. The user verifies the email address and signs in.
3. Onboarding asks for a coastal location by search, device location, or map
   selection.
4. The product resolves the point to a timezone and suitable public data
   sources, then previews those sources before saving the location.
5. The user chooses display units, fishing style, and personal wind and surf
   comfort thresholds.
6. The dashboard opens with the current outlook.

### Daily return

1. The user signs in and lands on the default saved location.
2. Above the fold, the user sees the go/no-go assessment, best practical
   window, material safety warning, forecast freshness, and confidence.
3. The user can inspect conditions, tides, hourly timing, attribution, and the
   reasons behind the assessment.
4. The user can switch saved locations or refresh an eligible stale forecast.

### Degraded data

1. Available sources still produce a partial forecast.
2. Missing observations are identified rather than replaced with estimates
   presented as measurements.
3. Stale observations display their age and do not appear current.
4. If evidence is insufficient for a responsible conclusion, the product says
   so and retains any independently available safety information.

## Information hierarchy

The signed-in dashboard presents information in this order:

1. Location and local forecast time.
2. Safety warnings that materially affect a fishing decision.
3. Go/no-go assessment and plain-language reason.
4. Best practical fishing window.
5. Forecast confidence and freshness.
6. Current wind, surf, water temperature, weather, and pressure.
7. Tide timing and hourly outlook.
8. Limited bait, rig, or target-category guidance supported by current inputs.
9. Provider, station, observation time, distance, and fallback details.

Visual prominence must follow this hierarchy. Detailed enrichment must not push
safety, timing, freshness, or confidence below decorative content.

## V1 capabilities

- Email/password account creation, verification, sign-in, sign-out, and
  password reset.
- Account-required onboarding and dashboard access.
- Text search, device location, and map selection for valid US coastal points.
- Stable resolved locations with timezone, marine zone, and candidate stations.
- NWS weather, wind, forecast, and alert data.
- NOAA CO-OPS tide and available environmental observations.
- NDBC buoy wind, wave, direction, period, and pressure observations.
- Deterministic sun, twilight, moon, and solunar calculations.
- Full, partial, stale, and unavailable forecast states.
- Explainable condition assessment, best window, and confidence reasons.
- Limited fishing guidance that cites its supporting inputs.
- Units, fishing style, wind threshold, surf threshold, default location, and a
  small ordered list of saved locations.
- Installable mobile web experience with an offline application shell.
- Public, non-personalized share pages added only after authenticated forecast
  behavior is launch-ready.

## Explicit non-goals

The following are excluded from the first public launch:

- Native iOS or Android applications.
- Payments, subscriptions, advertisements, affiliate commerce, or paywalls.
- Catch prediction, guaranteed bite claims, or scientific-accuracy claims not
  supported by a validation study.
- The legacy 851-species catalogue as a complete product surface.
- Full regulations aggregation or legal-compliance advice.
- Catch logging, catch photos, community feeds, social features, and leaderboards.
- Live cameras, water-quality enrichment, bathymetry, river discharge, fire,
  drought, aviation, FAO, or broad ArcGIS enrichment.
- Email or push fishing-condition alerts.
- OAuth, passkeys, anonymous accounts, or guest personalization.
- Historical migration of prototype users, sessions, catches, favorites, or
  cached forecasts.
- Background location tracking or retaining unnecessary precise coordinates.
- Redis, a distributed job queue, or multi-region infrastructure.

Adding a non-goal requires an explicit product decision and roadmap change; it
must not enter v1 indirectly as part of an implementation sprint.

## Product vocabulary

### Forecast state

- **Fresh:** The assembled snapshot and its decision-critical inputs are within
  their documented freshness windows.
- **Stale:** A previously valid snapshot is outside its freshness window but is
  still useful as clearly aged fallback information.
- **Partial:** The snapshot is current enough to display, but one or more
  decision-relevant providers or measurements are unavailable.
- **Unavailable:** Evidence is insufficient to produce the core assessment.
  Independently available warnings may still be shown.

State is categorical. It is not a fishing-quality score.

### Confidence

Confidence describes evidence quality, not expected fishing success. It is
derived from source coverage, observation age, station distance, and fallback
use. The interface must always show the reasons for reduced confidence.

### Go/no-go assessment

The assessment summarizes conditions relative to documented product rules and
the user's comfort thresholds. It is advisory, not a safety guarantee. It must
not hide an official warning or imply that a high score overrides one.

### Best window

The best window is the most favorable practical period within the displayed
forecast horizon according to available weather, tide, light, and comfort
inputs. It is not a prediction that fish will bite.

### Observation, forecast, and fallback

- **Observation:** A measured value with provider, station, and observation time.
- **Forecast:** A provider-issued future value with provider and valid period.
- **Fallback:** Previously obtained or lower-specificity information used when a
  preferred value is unavailable. A fallback is always labeled.

The interface and API must not describe a seasonal average, inferred value, or
fallback as a current observation.

## Attribution and evidence rules

Every displayed decision-relevant value must retain:

- provider name;
- station or forecast-zone identifier when applicable;
- observed, issued, or valid time;
- normalized unit;
- source distance when station distance affects relevance;
- freshness status; and
- fallback status and reason.

Additional rules:

- Provider errors must never be rendered as zero-valued measurements.
- Unit conversion occurs after raw value and unit capture.
- Official alerts remain clearly attributed and are not paraphrased in ways
  that weaken severity or instruction.
- A recommendation must name the conditions that support it.
- Conflicting provider values remain traceable; the API must not silently blend
  them into an unexplained number.
- Raw provider payloads may be retained only under a documented retention and
  privacy policy and must not be returned wholesale to browsers.

## Launch measures

The launch gate is reliable daily use, measured with privacy-safe events and
server telemetry.

### Required technical measures

- At least 99% of authenticated dashboard requests return a rendered full,
  partial, or clearly stale state rather than an unhandled error during beta.
- At least 95% of valid coastal resolution requests complete successfully.
- Warm cached forecast API latency has a p95 below 750 ms.
- Cold forecast requests terminate within the documented request deadline; no
  upstream dependency can wait indefinitely.
- No decision-relevant measurement appears without provider and time metadata.
- Automated launch-journey tests pass at 320, 390, and 768 CSS pixel widths.
- The launch journey has no known critical or serious automated accessibility
  violations and passes the manual keyboard and screen-reader checklist.
- Backup restoration and release rollback are demonstrated before public launch.

### Required product measures

- At least 80% of beta users who verify an account successfully resolve a
  location and view their first forecast.
- At least 80% of beta forecast views display enough evidence to produce a full
  or partial assessment rather than `unavailable`.
- Beta feedback shows that at least 4 of 5 moderated users can identify the
  recommendation, best window, freshness, and confidence without assistance.
- Every launch-blocking beta report is resolved, explicitly accepted with a
  documented reason, or converted into a launch constraint.

These are initial decision thresholds, not promises of traffic volume or
forecast skill. They should change only through a documented product decision.

## Product constraints

- Mobile-first responsive web is the only launch client.
- Core browsing requires a verified account.
- Any supported US coastal point is in scope; arbitrary global coordinates are not.
- Precise coordinates are rounded or discarded once they are no longer needed
  for resolution. Logs and analytics must not contain precise coordinates.
- Near-free operating cost is preferred, but production data cannot live on an
  ephemeral filesystem and the API cannot intentionally accept minute-long
  idle wake-ups.
- The existing Flask app is a behavior reference, not the target architecture.
- PostgreSQL is the sole v1 persistent store. Redis and job queues are excluded.

## Feature-to-need traceability

| Capability | User need |
| --- | --- |
| Account and saved preferences | Avoid repeating setup during daily checks. |
| Search, GPS, and map selection | Forecast the place the user actually fishes. |
| Go/no-go summary | Make the trip decision quickly. |
| Best window | Decide when to arrive. |
| Safety warnings | Avoid burying material risk beneath fishing advice. |
| Conditions and tides | Verify the recommendation against familiar evidence. |
| Freshness and confidence | Understand whether the answer is trustworthy. |
| Attribution | Inspect where important values came from. |
| Partial and stale states | Receive an honest answer during provider failures. |
| Limited fishing guidance | Translate conditions into a practical starting point. |
| Saved locations | Compare and revisit regular fishing spots efficiently. |
| Installable mobile web | Reach the product quickly from a phone. |

Any launch UI element not traceable to a row in this table requires product
review before implementation.

## Sprint 2 exit criteria

- [x] Primary user and supported geography are explicit.
- [x] First-use, return-use, and degraded-data journeys are documented.
- [x] V1 capabilities and non-goals are bounded.
- [x] Forecast vocabulary separates evidence quality from fishing quality.
- [x] Attribution and missing-data rules are explicit.
- [x] Technical and product launch measures are testable.
- [x] Every planned v1 capability maps to a user need.
- [x] No application code, schemas, or deployment behavior changed.
