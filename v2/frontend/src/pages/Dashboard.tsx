import { useEffect, useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import { useAuth } from '../context/AuthContext'
import { ForecastView } from '../components/ForecastView'

interface SavedLocation {
  id: string
  label: string
  lat: number
  lng: number
  is_default: boolean
}

const MAX_LOCATIONS = 5

export function Dashboard() {
  const { user } = useAuth()
  const [locations, setLocations] = useState<SavedLocation[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)

  const refresh = async () => {
    try {
      const list = await apiRequest<SavedLocation[]>('/locations', { auth: true })
      setLocations(list)
      setSelectedId((current) => current ?? list.find((l) => l.is_default)?.id ?? list[0]?.id ?? null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load your locations')
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selected = locations?.find((l) => l.id === selectedId) ?? null

  return (
    <div className="page">
      <h1>Hey, {user?.email.split('@')[0]}</h1>

      {locations && locations.length > 1 && (
        <div className="field">
          <label htmlFor="location-select">Location</label>
          <select
            id="location-select"
            value={selectedId ?? ''}
            onChange={(e) => setSelectedId(e.target.value)}
          >
            {locations.map((loc) => (
              <option key={loc.id} value={loc.id}>
                {loc.label}
              </option>
            ))}
          </select>
        </div>
      )}

      {error && <div className="field-error">{error}</div>}

      {locations && locations.length === 0 && !showAddForm && (
        <div className="card">
          <p>No saved locations yet.</p>
          <button className="button" onClick={() => setShowAddForm(true)}>
            Add your first spot
          </button>
        </div>
      )}

      {selected && (
        <>
          <h2 style={{ marginBottom: 0 }}>{selected.label}</h2>
          <ForecastView locationId={selected.id} />
        </>
      )}

      {locations && locations.length > 0 && locations.length < MAX_LOCATIONS && (
        <button
          className="button button--secondary"
          style={{ marginTop: '1rem' }}
          onClick={() => setShowAddForm((v) => !v)}
        >
          {showAddForm ? 'Cancel' : 'Add a location'}
        </button>
      )}

      {showAddForm && (
        <AddLocationForm
          onAdded={() => {
            setShowAddForm(false)
            refresh()
          }}
        />
      )}
    </div>
  )
}

function AddLocationForm({ onAdded }: { onAdded: () => void }) {
  const [label, setLabel] = useState('')
  const [lat, setLat] = useState('')
  const [lng, setLng] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await apiRequest('/locations', {
        method: 'POST',
        auth: true,
        body: { label, lat: Number(lat), lng: Number(lng) },
      })
      onAdded()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add location')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} className="card" style={{ marginTop: '1rem' }}>
      <div className="field">
        <label htmlFor="label">Label</label>
        <input id="label" required value={label} onChange={(e) => setLabel(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="lat">Latitude</label>
        <input
          id="lat"
          type="number"
          step="any"
          required
          value={lat}
          onChange={(e) => setLat(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="lng">Longitude</label>
        <input
          id="lng"
          type="number"
          step="any"
          required
          value={lng}
          onChange={(e) => setLng(e.target.value)}
        />
      </div>
      {error && <div className="field-error">{error}</div>}
      <button className="button" type="submit" disabled={submitting}>
        Save location
      </button>
    </form>
  )
}
