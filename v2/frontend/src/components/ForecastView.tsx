import { useEffect, useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import type { Forecast } from '../api/forecast'

export function ForecastView({ locationId }: { locationId: string }) {
  const [forecast, setForecast] = useState<Forecast | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setForecast(null)
    setError(null)
    setLoading(true)

    apiRequest<Forecast>(`/forecast/${locationId}`, { auth: true })
      .then((data) => {
        if (!cancelled) setForecast(data)
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load the forecast')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [locationId])

  if (loading) {
    return (
      <div className="card" style={{ marginTop: '1rem' }}>
        Checking conditions…
      </div>
    )
  }

  if (error) {
    return (
      <div className="card" style={{ marginTop: '1rem' }}>
        <p className="field-error">{error}</p>
      </div>
    )
  }

  if (!forecast) return null

  const { conditions, best_times, species } = forecast

  return (
    <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {conditions && (
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.75rem' }}>
            <span style={{ fontSize: '2rem', fontWeight: 700 }}>
              {conditions.fishability_score}
            </span>
            <span className="text-muted">/ 100 — {conditions.verdict}</span>
          </div>
          <p>{conditions.summary}</p>
          <p className="text-muted" style={{ fontSize: '0.9rem' }}>
            {conditions.wind} wind · {conditions.waves} waves
            {conditions.water_temp_f ? ` · ${conditions.water_temp_f}°F water` : ''}
          </p>
        </div>
      )}

      {best_times && best_times.length > 0 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Best time to fish today</h3>
          <p style={{ fontWeight: 600 }}>{best_times[0].window}</p>
          <p className="text-muted">{best_times[0].reason}</p>
        </div>
      )}

      {species && species.length > 0 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>What's biting</h3>
          {species.slice(0, 5).map((sp) => (
            <div
              key={sp.rank}
              style={{
                paddingBottom: '0.75rem',
                marginBottom: '0.75rem',
                borderBottom: '1px solid var(--color-border)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <strong>{sp.name}</strong>
                <span className="text-muted">{sp.activity}</span>
              </div>
              <p className="text-muted" style={{ fontSize: '0.9rem', margin: '0.25rem 0' }}>
                {sp.bait} · {sp.rig}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
