import { ImageResponse } from 'next/og'

/**
 * A real favicon, generated at build time (no `public/` image asset
 * needed) rather than left unset -- a bare Next.js app with no `icon`/
 * `favicon` file leaves the browser requesting `/favicon.ico`, which
 * 404s and shows up as a real console error (Lighthouse's
 * `errors-in-console` audit, run for real against the production build
 * as part of a sprint 39 responsive-polish pass, caught exactly this).
 * The mark itself is the Saltline logomark (a horizon line with a
 * sunrise arc above it and a wave below -- the literal line where tide
 * meets shore) in the rebrand's real teal/coral palette
 * (`app/globals.css`), not a placeholder.
 */
export const size = { width: 32, height: 32 }
export const contentType = 'image/png'

export default function Icon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#1c4a56',
          borderRadius: 7,
        }}
      >
        <svg width="21" height="21" viewBox="0 0 24 24" fill="none">
          <path d="M2 11 L22 11" stroke="#ffffff" strokeWidth="2" strokeLinecap="round" />
          <path
            d="M6 11 A6 6 0 0 1 18 11"
            stroke="#ff8552"
            strokeWidth="2"
            strokeLinecap="round"
          />
          <path
            d="M2 16 Q5 14 8 16 T14 16 T20 16"
            stroke="#ffffff"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
      </div>
    ),
    { ...size },
  )
}
