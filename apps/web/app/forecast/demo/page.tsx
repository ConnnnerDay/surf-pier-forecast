import { redirect } from 'next/navigation'

/**
 * Retired in favor of app/forecast/[locationId]/page.tsx, which does
 * everything this fixed-location proof page did and more (any real
 * location, not just Wrightsville Beach). Kept as a redirect rather
 * than deleted outright, in case anything still links here.
 */
export default function ForecastDemoPage() {
  redirect('/forecast/wrightsville-beach-nc')
}
