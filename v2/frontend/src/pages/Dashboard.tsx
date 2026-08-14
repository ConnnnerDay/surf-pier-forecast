import { useEffect, useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import { MAX_SAVED_LOCATIONS, type SavedLocation } from '../api/locations'
import { useAuth } from '../context/AuthContext'
import { ForecastView } from '../components/ForecastView'

export function Dashboard() {
  const { user } = useAuth()
  const [locations, setLocations] = useState<SavedLocation[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

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

  const setDefault = async (id: string) => {
    setBusyId(id)
    setError(null)
    try {
      await apiRequest(`/locations/${id}`, { method: 'PATCH', auth: true, body: { is_default: true } })
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not set default location')
    } finally {
      setBusyId(null)
    }
  }

  const removeLocation = async (id: string) => {
    setBusyId(id)
    setError(null)
    try {
      await apiRequest(`/locations/${id}`, { method: 'DELETE', auth: true })
      if (selectedId === id) setSelectedId(null)
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove location')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="page">
      <h1>Hey, {user?.email.split('@')[0]}</h1>

      {locations && locations.length > 0 && (
        <div className="field" role="tablist" aria-label="Saved locations">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
            {locations.map((loc) => (
              <div
                key={loc.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.25rem',
                  border: '1px solid var(--color-border)',
                  borderRadius: 'var(--radius)',
                  padding: '0.25rem 0.25rem 0.25rem 0.75rem',
                  background: loc.id === selectedId ? 'var(--color-surface)' : 'transparent',
                }}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={loc.id === selectedId}
                  onClick={() => setSelectedId(loc.id)}
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    color: loc.id === selectedId ? 'var(--color-text)' : 'var(--color-text-muted)',
                    fontWeight: loc.id === selectedId ? 700 : 400,
                    padding: '0.25rem 0',
                  }}
                >
                  {loc.label}
                </button>
                <button
                  type="button"
                  onClick={() => setDefault(loc.id)}
                  disabled={loc.is_default || busyId === loc.id}
                  title={loc.is_default ? 'Default location' : 'Set as default'}
                  aria-label={loc.is_default ? 'Default location' : `Set ${loc.label} as default`}
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: loc.is_default ? 'default' : 'pointer',
                    opacity: loc.is_default ? 1 : 0.4,
                    padding: '0.25rem',
                  }}
                >
                  {loc.is_default ? '★' : '☆'}
                </button>
                <button
                  type="button"
                  onClick={() => removeLocation(loc.id)}
                  disabled={busyId === loc.id}
                  aria-label={`Remove ${loc.label}`}
                  className="button button--secondary"
                  style={{ padding: '0 0.5rem' }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
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

      {locations && locations.length > 0 && locations.length < MAX_SAVED_LOCATIONS && (
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
