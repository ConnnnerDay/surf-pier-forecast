import { ImageResponse } from 'next/og'

/**
 * Sprint 49 ("SEO and sharing"): a real Open Graph / Twitter card image
 * for link previews, generated at build time via `next/og`'s
 * `ImageResponse` (same technique as `app/icon.tsx`'s favicon -- no
 * `public/` asset, no design tool). Applies to every page under `app/`
 * that doesn't define its own more specific `opengraph-image` (none do
 * yet -- a per-location card is a follow-up, not attempted here). Uses
 * the design system's own teal/coral placeholder palette
 * (`app/globals.css`), not an arbitrary color, so this implies no
 * branding decision beyond the one already on record (sprint 27's row).
 */
export const alt = 'Surf & Pier Forecast'
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
          background: '#0b1f2a',
        }}
      >
        <svg width="120" height="120" viewBox="0 0 20 20" fill="none">
          <path
            d="M2 12c1.5-2 3-2 4.5 0s3 2 4.5 0 3-2 4.5 0"
            stroke="#ff8552"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
          <path
            d="M2 7c1.5-2 3-2 4.5 0s3 2 4.5 0 3-2 4.5 0"
            stroke="#ffffff"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
        <div
          style={{
            fontSize: 64,
            fontWeight: 700,
            color: '#eef5f6',
          }}
        >
          Surf &amp; Pier Forecast
        </div>
        <div style={{ fontSize: 28, color: '#a9bdc4' }}>
          Clear go/no-go fishing forecasts for surf and pier anglers
        </div>
      </div>
    ),
    { ...size },
  )
}
