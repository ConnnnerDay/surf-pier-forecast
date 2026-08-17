import type { NextConfig } from 'next'

// R3 skeleton: no rewrites/env wiring to apps/api yet. The browser must
// never call FastAPI directly (docs/CANONICAL_ROADMAP.md) — the signed
// internal request path from this BFF to apps/api lands with auth in
// Phase 1/2 sprints, not here.
const nextConfig: NextConfig = {}

export default nextConfig
