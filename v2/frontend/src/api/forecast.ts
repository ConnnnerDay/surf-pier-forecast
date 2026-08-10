// Partial typing of v1's domain/forecast.py generate_forecast() response —
// only the fields the dashboard currently renders. The real response has
// many more fields (activity_timeline, rig_recommendations, calendar,
// spawning, etc.) that aren't surfaced in the UI yet; add to this as the
// dashboard grows rather than typing the whole payload up front.
export interface BestTimeWindow {
  window: string
  reason: string
  quality: string
}

export interface RankedSpecies {
  rank: number
  name: string
  score: number
  activity: string
  explanation: string
  bait: string
  rig: string
}

export interface Forecast {
  location_id: string
  location_name: string
  conditions?: {
    wind?: string
    waves?: string
    verdict?: string
    water_temp_f?: number
    fishability_score?: number
    summary?: string
  }
  best_times?: BestTimeWindow[]
  species?: RankedSpecies[]
  sources_used?: string[]
  fallbacks_triggered?: string[]
}
