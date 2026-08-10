import { useEffect, useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import {
  ACCESSIBILITY_NEEDS,
  FISHING_STYLES,
  GEAR_LIMITATIONS,
  type Profile as ProfileData,
} from '../api/profile'
import { PasskeySettings } from '../components/PasskeySettings'
import { TwoFactorSettings } from '../components/TwoFactorSettings'
import { useTheme } from '../context/ThemeContext'
import { useUnits } from '../context/UnitsContext'

function toggleInList(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

export function Profile() {
  const { setTheme } = useTheme()
  const { setUnits } = useUnits()

  const [profile, setProfile] = useState<ProfileData | null>(null)
  const [speciesInput, setSpeciesInput] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    apiRequest<ProfileData>('/profile', { auth: true })
      .then(setProfile)
      .catch((err) =>
        setLoadError(err instanceof ApiError ? err.message : 'Could not load profile'),
      )
  }, [])

  if (loadError) return <div className="page field-error">{loadError}</div>
  if (!profile) return <div className="page">Loading…</div>

  const update = <K extends keyof ProfileData>(key: K, value: ProfileData[K]) => {
    setProfile({ ...profile, [key]: value })
    setSaved(false)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setSaveError(null)
    try {
      const result = await apiRequest<ProfileData>('/profile', {
        method: 'PATCH',
        auth: true,
        body: profile,
      })
      setProfile(result)
      setTheme(result.theme)
      setUnits(result.units)
      setSaved(true)
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : 'Could not save profile')
    } finally {
      setSaving(false)
    }
  }

  const addSpecies = () => {
    const name = speciesInput.trim()
    if (name && !profile.target_species.includes(name)) {
      update('target_species', [...profile.target_species, name])
    }
    setSpeciesInput('')
  }

  return (
    <div className="page">
      <h1>Profile</h1>
      <form onSubmit={save}>
        <h3>Comfort thresholds</h3>
      <div className="field">
        <label htmlFor="max_wind_mph">Max wind (mph)</label>
        <input
          id="max_wind_mph"
          type="number"
          min={0}
          value={profile.max_wind_mph ?? ''}
          onChange={(e) =>
            update('max_wind_mph', e.target.value === '' ? null : Number(e.target.value))
          }
        />
      </div>
      <div className="field">
        <label htmlFor="max_surf_ft">Max surf (ft)</label>
        <input
          id="max_surf_ft"
          type="number"
          min={0}
          value={profile.max_surf_ft ?? ''}
          onChange={(e) =>
            update('max_surf_ft', e.target.value === '' ? null : Number(e.target.value))
          }
        />
      </div>

      <h3>Experience</h3>
      <div className="field">
        <select
          value={profile.experience_level}
          onChange={(e) => update('experience_level', e.target.value as ProfileData['experience_level'])}
        >
          <option value="beginner">Beginner</option>
          <option value="intermediate">Intermediate</option>
          <option value="advanced">Advanced</option>
        </select>
      </div>

      <h3>Fishing styles</h3>
      <div className="field">
        {FISHING_STYLES.map((style) => (
          <label key={style} style={{ display: 'block' }}>
            <input
              type="checkbox"
              checked={profile.fishing_styles.includes(style)}
              onChange={() => update('fishing_styles', toggleInList(profile.fishing_styles, style))}
            />{' '}
            {style}
          </label>
        ))}
      </div>

      <h3>Gear / accessibility</h3>
      <div className="field">
        {GEAR_LIMITATIONS.map((item) => (
          <label key={item} style={{ display: 'block' }}>
            <input
              type="checkbox"
              checked={profile.gear_limitations.includes(item)}
              onChange={() =>
                update('gear_limitations', toggleInList(profile.gear_limitations, item))
              }
            />{' '}
            {item}
          </label>
        ))}
        {ACCESSIBILITY_NEEDS.map((item) => (
          <label key={item} style={{ display: 'block' }}>
            <input
              type="checkbox"
              checked={profile.accessibility_needs.includes(item)}
              onChange={() =>
                update('accessibility_needs', toggleInList(profile.accessibility_needs, item))
              }
            />{' '}
            {item}
          </label>
        ))}
      </div>

      <h3>Target species</h3>
      <div className="field" style={{ flexDirection: 'row', gap: '0.5rem' }}>
        <input
          value={speciesInput}
          onChange={(e) => setSpeciesInput(e.target.value)}
          placeholder="e.g. Redfish"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              addSpecies()
            }
          }}
        />
        <button type="button" className="button button--secondary" onClick={addSpecies}>
          Add
        </button>
      </div>
      {profile.target_species.length > 0 && (
        <ul style={{ paddingLeft: '1.25rem' }}>
          {profile.target_species.map((sp) => (
            <li key={sp}>
              {sp}{' '}
              <button
                type="button"
                className="button button--secondary"
                style={{ padding: '0 0.4rem' }}
                onClick={() =>
                  update(
                    'target_species',
                    profile.target_species.filter((s) => s !== sp),
                  )
                }
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <h3>Display</h3>
      <div className="field">
        <label htmlFor="units">Units</label>
        <select
          id="units"
          value={profile.units}
          onChange={(e) => update('units', e.target.value as ProfileData['units'])}
        >
          <option value="imperial">Imperial</option>
          <option value="metric">Metric</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="theme">Theme</label>
        <select
          id="theme"
          value={profile.theme}
          onChange={(e) => update('theme', e.target.value as ProfileData['theme'])}
        >
          <option value="system">System</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
      </div>

        {saveError && <div className="field-error">{saveError}</div>}
        {saved && <p className="text-muted">Saved.</p>}
        <button className="button" type="submit" disabled={saving}>
          Save profile
        </button>
      </form>

      <div style={{ marginTop: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <PasskeySettings />
        <TwoFactorSettings />
      </div>
    </div>
  )
}
