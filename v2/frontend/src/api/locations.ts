export interface SavedLocation {
  id: string
  label: string
  lat: number
  lng: number
  curated_location_id: string | null
  is_default: boolean
}

export interface SavedLocationCreate {
  label: string
  lat: number
  lng: number
}

export interface SavedLocationUpdate {
  label?: string
  is_default?: boolean
}

export const MAX_SAVED_LOCATIONS = 5
