import { useEffect, useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import {
  COASTAL_STATES,
  STATUS_LABEL,
  VERDICT_LABEL,
  type LegalCatchResponse,
  type RegulationOut,
} from '../api/regulations'

export function Regulations() {
  const [allSpecies, setAllSpecies] = useState<string[]>([])
  const [species, setSpecies] = useState('')
  const [state, setState] = useState('')

  const [regulation, setRegulation] = useState<RegulationOut | null>(null)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const [looking, setLooking] = useState(false)

  const [length, setLength] = useState('')
  const [catchResult, setCatchResult] = useState<LegalCatchResponse | null>(null)
  const [catchError, setCatchError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    apiRequest<string[]>('/regulations/species', { auth: true })
      .then(setAllSpecies)
      .catch(() => setAllSpecies([]))
  }, [])

  const lookup = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!species.trim() || !state) return
    setLooking(true)
    setLookupError(null)
    setCatchResult(null)
    setCatchError(null)
    try {
      const result = await apiRequest<RegulationOut>(
        `/regulations/lookup?species=${encodeURIComponent(species.trim())}&state=${state}`,
        { auth: true },
      )
      setRegulation(result)
    } catch (err) {
      setRegulation(null)
      setLookupError(err instanceof ApiError ? err.message : 'Could not look up this regulation')
    } finally {
      setLooking(false)
    }
  }

  const checkCatch = async (e: React.FormEvent) => {
    e.preventDefault()
    const lengthIn = Number(length)
    if (!regulation || !length || lengthIn <= 0) return
    setChecking(true)
    setCatchError(null)
    try {
      const result = await apiRequest<LegalCatchResponse>('/regulations/legal-catch', {
        method: 'POST',
        auth: true,
        body: { species: regulation.species, state: regulation.state, length_in: lengthIn },
      })
      setCatchResult(result)
    } catch (err) {
      setCatchResult(null)
      setCatchError(err instanceof ApiError ? err.message : 'Could not check this catch')
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="page">
      <h1>Regulations</h1>
      <p className="text-muted">
        Advisory only — regulations change and vary by sub-area. Always verify with the official
        source before keeping a fish.
      </p>

      <form onSubmit={lookup}>
        <div className="field">
          <label htmlFor="species">Species</label>
          <input
            id="species"
            list="species-options"
            value={species}
            onChange={(e) => setSpecies(e.target.value)}
            placeholder="e.g. Red drum"
            autoComplete="off"
          />
          <datalist id="species-options">
            {allSpecies.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        </div>
        <div className="field">
          <label htmlFor="state">State</label>
          <select id="state" value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">Select a state…</option>
            {COASTAL_STATES.map((s) => (
              <option key={s.code} value={s.code}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        {lookupError && <div className="field-error">{lookupError}</div>}
        <button className="button" type="submit" disabled={looking || !species.trim() || !state}>
          {looking ? 'Looking up…' : 'Look up regulation'}
        </button>
      </form>

      {regulation && (
        <div className="card" style={{ marginTop: '1.25rem' }}>
          <h3 style={{ marginTop: 0 }}>
            {regulation.species} — {regulation.state}
          </h3>
          <p>
            <strong>Status:</strong> {STATUS_LABEL[regulation.status] ?? regulation.status}
          </p>
          {regulation.min_size && (
            <p>
              <strong>Minimum size:</strong> {regulation.min_size}
            </p>
          )}
          {regulation.slot && (
            <p>
              <strong>Slot limit:</strong> {regulation.slot}
            </p>
          )}
          {regulation.bag_limit && (
            <p>
              <strong>Bag limit:</strong> {regulation.bag_limit}
            </p>
          )}
          {regulation.season && (
            <p>
              <strong>Season:</strong> {regulation.season}
            </p>
          )}
          {regulation.gear && (
            <p>
              <strong>Gear:</strong> {regulation.gear}
            </p>
          )}
          {regulation.notes && (
            <p>
              <strong>Notes:</strong> {regulation.notes}
            </p>
          )}
          {regulation.is_stale && (
            <p className="field-error">This data may be out of date — verify before fishing.</p>
          )}
          {regulation.official_source && (
            <p>
              <a href={regulation.official_source} target="_blank" rel="noreferrer">
                Official source
              </a>
            </p>
          )}

          <h4>Check a catch</h4>
          <form onSubmit={checkCatch}>
            <div className="field" style={{ flexDirection: 'row', gap: '0.5rem' }}>
              <input
                type="number"
                min={0}
                step="0.1"
                value={length}
                onChange={(e) => setLength(e.target.value)}
                placeholder="Length (in)"
              />
              <button className="button" type="submit" disabled={checking || !length}>
                {checking ? 'Checking…' : 'Check'}
              </button>
            </div>
          </form>
          {catchError && <div className="field-error">{catchError}</div>}
          {catchResult && (
            <p
              className={catchResult.legal === false ? 'field-error' : undefined}
              style={catchResult.legal ? { color: 'var(--color-primary)', fontWeight: 600 } : undefined}
            >
              <strong>{VERDICT_LABEL[catchResult.verdict]}.</strong> {catchResult.reason}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
