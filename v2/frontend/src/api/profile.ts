export interface Profile {
  max_wind_mph: number | null
  max_surf_ft: number | null
  fishing_styles: string[]
  gear_limitations: string[]
  accessibility_needs: string[]
  experience_level: 'beginner' | 'intermediate' | 'advanced'
  target_species: string[]
  units: 'imperial' | 'metric'
  theme: 'system' | 'light' | 'dark'
  onboarding_completed: boolean
}

export type ProfileUpdate = Partial<Profile>

export const FISHING_STYLES = ['surf', 'pier', 'kayak', 'inshore', 'offshore'] as const
export const GEAR_LIMITATIONS = ['light-tackle-only', 'no-boat', 'no-waders'] as const
export const ACCESSIBILITY_NEEDS = ['limited-mobility', 'no-long-walks', 'pier-only'] as const
