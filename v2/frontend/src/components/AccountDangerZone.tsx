import { useState } from 'react'
import { apiRequest, ApiError } from '../api/client'
import type { AccountExport } from '../api/account'
import { useAuth } from '../context/AuthContext'

export function AccountDangerZone() {
  const { user, logout } = useAuth()

  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const [showDeleteForm, setShowDeleteForm] = useState(false)
  const [password, setPassword] = useState('')
  const [confirmText, setConfirmText] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  if (!user) return null

  const downloadExport = async () => {
    setExporting(true)
    setExportError(null)
    try {
      const data = await apiRequest<AccountExport>('/account/export', { auth: true })
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'fishing-forecast-data.json'
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setExportError(err instanceof ApiError ? err.message : 'Could not export your data')
    } finally {
      setExporting(false)
    }
  }

  const deleteAccount = async (e: React.FormEvent) => {
    e.preventDefault()
    setDeleteError(null)
    setDeleting(true)
    try {
      await apiRequest('/account', {
        method: 'DELETE',
        auth: true,
        body: { password: password || null },
      })
      // A hard redirect, not client-side navigate(): clearing the user
      // here also makes ProtectedRoute (still mounted on this /profile
      // route) redirect to /login on the same render, and that race is
      // easy to lose against an in-SPA navigate() call. A full page load
      // sidesteps it entirely and leaves no stale state behind anyway.
      logout()
      window.location.href = '/'
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : 'Could not delete your account')
    } finally {
      setDeleting(false)
    }
  }

  const canSubmitDelete = confirmText === 'DELETE' && (!user.has_password || password.length > 0)

  return (
    <div className="card" style={{ borderColor: 'var(--color-danger)' }}>
      <h3 style={{ marginTop: 0 }}>Your data</h3>
      <p className="text-muted">
        Download everything we have on your account — profile, saved locations, and account
        details — as a JSON file.
      </p>
      {exportError && <div className="field-error">{exportError}</div>}
      <button className="button button--secondary" onClick={downloadExport} disabled={exporting}>
        {exporting ? 'Preparing…' : 'Export my data'}
      </button>

      <h3>Delete account</h3>
      {!showDeleteForm ? (
        <>
          <p className="text-muted">
            Permanently deletes your account, profile, and saved locations. This can't be undone.
          </p>
          <button
            className="button button--secondary"
            style={{ color: 'var(--color-danger)', borderColor: 'var(--color-danger)' }}
            onClick={() => setShowDeleteForm(true)}
          >
            Delete my account
          </button>
        </>
      ) : (
        <form onSubmit={deleteAccount}>
          <p className="text-muted">
            This permanently deletes your account and all associated data. It cannot be undone.
          </p>
          {user.has_password && (
            <div className="field">
              <label htmlFor="delete-password">Password</label>
              <input
                id="delete-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          )}
          <div className="field">
            <label htmlFor="delete-confirm">
              Type <strong>DELETE</strong> to confirm
            </label>
            <input
              id="delete-confirm"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
            />
          </div>
          {deleteError && <div className="field-error">{deleteError}</div>}
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              className="button"
              type="submit"
              style={{ background: 'var(--color-danger)' }}
              disabled={!canSubmitDelete || deleting}
            >
              {deleting ? 'Deleting…' : 'Permanently delete my account'}
            </button>
            <button
              type="button"
              className="button button--secondary"
              onClick={() => {
                setShowDeleteForm(false)
                setPassword('')
                setConfirmText('')
                setDeleteError(null)
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  )
}
