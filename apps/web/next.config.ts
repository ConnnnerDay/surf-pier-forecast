import type { NextConfig } from 'next'

// R3 skeleton note (still true): no rewrites/env wiring to apps/api here.
// The browser must never call FastAPI directly (docs/CANONICAL_ROADMAP.md)
// — the signed internal request path from this BFF to apps/api is a
// server-side-only fetch (lib/internal-api-client.ts), not a rewrite.

// Sprint 44's remaining "security hardening" piece (CSP + security
// headers), adapted from the legacy Flask app's `_set_security_headers`
// (app.py) rather than ported verbatim: apps/web has no third-party
// scripts, external fonts, or non-self image origins today (unlike the
// legacy app's map tiles/CDN allowances), so this CSP is deliberately
// stricter — self-only across every directive, no allow-listed external
// hosts to grow stale. `'unsafe-inline'` on `script-src`/`style-src` is
// still required without a nonce: Next.js inlines its hydration payload
// and (in dev) React's error-overlay styles, per
// node_modules/next/dist/docs/01-app/02-guides/content-security-policy.md's
// "Without Nonces" section — nonce-based CSP is documented there as
// requiring *every* page to be dynamically rendered (no static
// prerendering), a real cost this app isn't paying yet for pages with no
// user data and no third-party scripts to justify it. `Permissions-Policy`
// locks geolocation/camera fully closed (unlike the legacy app, which
// scoped them `self` for its device-geolocation and catch-log-photo
// features) since neither exists in apps/web yet — sprint 31's still-open
// device-geolocation sub-item is the natural point to loosen this, not
// before.
const isDev = process.env.NODE_ENV === 'development'

const CSP_DIRECTIVES = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ''}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  // Upgrading insecure requests only makes sense once this app is
  // actually served over TLS (Vercel, per docs/CANONICAL_ROADMAP.md's
  // technical contract) — in dev it would make the browser try to
  // re-fetch every same-origin asset over https://localhost, which
  // nothing is listening on, breaking the page.
  ...(isDev ? [] : ['upgrade-insecure-requests']),
]

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          {
            key: 'Permissions-Policy',
            value: 'interest-cohort=(), geolocation=(), microphone=(), camera=()',
          },
          { key: 'Content-Security-Policy', value: CSP_DIRECTIVES.join('; ') },
          { key: 'X-Permitted-Cross-Domain-Policies', value: 'none' },
          // Safe to send unconditionally: browsers ignore HSTS response
          // headers received over plain HTTP (dev), so this has no
          // effect until the app is actually served over TLS.
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=31536000; includeSubDomains',
          },
        ],
      },
    ]
  },
}

export default nextConfig
