// Mirrors v2/backend/app/schemas/regulations.py.
export interface RegulationOut {
  species: string
  state: string
  status: string
  min_size: string | null
  slot: string | null
  bag_limit: string | null
  season: string | null
  gear: string | null
  notes: string | null
  official_source: string | null
  is_stale: boolean
}

export type Verdict = 'legal' | 'too_small' | 'too_large' | 'cannot_target' | 'unknown'

export interface LegalCatchRequest {
  species: string
  state: string
  length_in: number
}

export interface LegalCatchResponse {
  verdict: Verdict
  legal: boolean | null
  reason: string
  min_size_in: number | null
  max_size_in: number | null
  regulation: RegulationOut
}

// Continental US coastal states covered by the regulations dataset — see
// docs/V2_PLAN.md "Regulations coverage" (Alaska/Hawaii/territories are out
// of scope for MVP; a stray "AZ" entry in the source data is not a coastal
// state and is intentionally omitted here).
export const COASTAL_STATES: { code: string; name: string }[] = [
  { code: 'AL', name: 'Alabama' },
  { code: 'CA', name: 'California' },
  { code: 'CT', name: 'Connecticut' },
  { code: 'DE', name: 'Delaware' },
  { code: 'FL', name: 'Florida' },
  { code: 'GA', name: 'Georgia' },
  { code: 'LA', name: 'Louisiana' },
  { code: 'MA', name: 'Massachusetts' },
  { code: 'MD', name: 'Maryland' },
  { code: 'ME', name: 'Maine' },
  { code: 'MS', name: 'Mississippi' },
  { code: 'NC', name: 'North Carolina' },
  { code: 'NH', name: 'New Hampshire' },
  { code: 'NJ', name: 'New Jersey' },
  { code: 'NY', name: 'New York' },
  { code: 'OR', name: 'Oregon' },
  { code: 'PA', name: 'Pennsylvania' },
  { code: 'RI', name: 'Rhode Island' },
  { code: 'SC', name: 'South Carolina' },
  { code: 'TX', name: 'Texas' },
  { code: 'VA', name: 'Virginia' },
  { code: 'WA', name: 'Washington' },
]

export const STATUS_LABEL: Record<string, string> = {
  legal: 'Open',
  prohibited: 'Prohibited',
  out_of_season: 'Closed (out of season)',
  catch_and_release: 'Catch & release only',
  unknown: 'Unknown — verify locally',
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  legal: 'Legal to keep',
  too_small: 'Too small — release',
  too_large: 'Too large — release',
  cannot_target: 'Cannot keep',
  unknown: 'Unknown — verify locally',
}
