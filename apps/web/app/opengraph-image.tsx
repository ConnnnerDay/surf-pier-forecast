import { ImageResponse } from 'next/og'

/**
 * A real Open Graph / Twitter card image for link previews, generated
 * at build time via `next/og`'s `ImageResponse` (same technique as
 * `app/icon.tsx`'s favicon -- no `public/` asset, no design tool).
 * Applies to every page under `app/` that doesn't define its own more
 * specific `opengraph-image` (none do yet -- a per-location card is a
 * follow-up, not attempted here). Uses the Saltline rebrand's real
 * palette and logomark (`app/globals.css`, `app/icon.tsx`), not the
 * earlier placeholder identity. No custom font is loaded here (Satori
 * needs actual font-file bytes, not a stylesheet link, and fetching
 * one at build time would need outbound network access this sandbox
 * doesn't have) -- falls back to `next/og`'s bundled default sans,
 * same as before this rebrand.
 */
export const alt = 'Saltline'
export const size = { width: 1200, height: 630 }
export const contentType = 'image/png'

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 32,
          background: '#0d2530',
        }}
      >
        <svg width="120" height="120" viewBox="0 0 24 24" fill="none">
          <path d="M2 11 L22 11" stroke="#ffffff" strokeWidth="1.6" strokeLinecap="round" />
          <path
            d="M6 11 A6 6 0 0 1 18 11"
            stroke="#ff8552"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
          <path
            d="M2 16 Q5 14 8 16 T14 16 T20 16"
            stroke="#ffffff"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
        <div
          style={{
            fontSize: 68,
            fontWeight: 700,
            color: '#eef5f6',
          }}
        >
          Saltline
        </div>
        <div style={{ fontSize: 28, color: '#9fc3cc' }}>
          Clear go/no-go fishing forecasts for surf and pier anglers
        </div>
      </div>
    ),
    { ...size },
  )
}
