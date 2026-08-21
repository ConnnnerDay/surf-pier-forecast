import { ImageResponse } from 'next/og'

/**
 * A real favicon, generated at build time (no `public/` image asset
 * needed) rather than left unset -- a bare Next.js app with no `icon`/
 * `favicon` file leaves the browser requesting `/favicon.ico`, which
 * 404s and shows up as a real console error (Lighthouse's
 * `errors-in-console` audit, run for real against the production build
 * as part of a sprint 39 responsive-polish pass, caught exactly this).
 * Uses the design system's own teal/coral placeholder palette
 * (`app/globals.css`'s `--color-primary`/`--color-accent`), not an
 * arbitrary color, so this doesn't imply a branding decision beyond
 * the one already on record (`docs/CANONICAL_ROADMAP.md`'s sprint 27
 * row).
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
          background: '#0e7480',
          borderRadius: 6,
        }}
      >
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <path
            d="M2 12c1.5-2 3-2 4.5 0s3 2 4.5 0 3-2 4.5 0"
            stroke="#ff8552"
            strokeWidth="2.2"
            strokeLinecap="round"
          />
          <path
            d="M2 7c1.5-2 3-2 4.5 0s3 2 4.5 0 3-2 4.5 0"
            stroke="#ffffff"
            strokeWidth="2.2"
            strokeLinecap="round"
          />
        </svg>
      </div>
    ),
    { ...size },
  )
}
