export interface AccountExportProfile {
  max_wind_mph: number | null
  max_surf_ft: number | null
  fishing_styles: string[]
  gear_limitations: string[]
  accessibility_needs: string[]
  experience_level: string
  target_species: string[]
  units: string
  theme: string
  onboarding_completed: boolean
}

export interface AccountExportLocation {
  id: string
  label: string
  lat: number
  lng: number
  is_default: boolean
}

export interface AccountExport {
  id: string
  email: string
  date_of_birth: string | null
  created_at: string
  has_password: boolean
  google_linked: boolean
  apple_linked: boolean
  totp_enabled: boolean
  profile: AccountExportProfile | null
  locations: AccountExportLocation[]
}
