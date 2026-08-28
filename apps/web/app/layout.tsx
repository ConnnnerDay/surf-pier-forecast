import type { Metadata, Viewport } from 'next'
import './globals.css'

// Sprint 49 ("SEO and sharing"): NEXT_PUBLIC_SITE_URL lets metadataBase
// (and every relative canonical/OpenGraph URL built from it, here and
// in per-page generateMetadata) resolve to the real deployed origin
// once one exists (docs/CANONICAL_ROADMAP.md's Phase 1 blockers 9/10 --
// Vercel isn't provisioned yet). Defaults to local dev's own origin so
// `next build`/`next dev` never need it set to produce valid URLs.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? 'http://localhost:3000'

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'Surf & Pier Forecast',
    template: '%s — Surf & Pier Forecast',
  },
  description:
    'Clear go/no-go fishing forecasts for surf and pier anglers. Working title — see docs/CANONICAL_ROADMAP.md sprint 27.',
  openGraph: {
    siteName: 'Surf & Pier Forecast',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
  },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#f4f7f8' },
    { media: '(prefers-color-scheme: dark)', color: '#0b1f2a' },
  ],
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
