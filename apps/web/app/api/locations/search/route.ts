import { type NextRequest, NextResponse } from 'next/server'
import { internalApiFetch } from '@/lib/internal-api-client'

// Live per-request proxy -- never cached.
export const dynamic = 'force-dynamic'

export type LocationSearchResult = {
  id: string
  name: string
  state: string
  lat: number
  lng: number
}

/**
 * BFF proxy for sprint 31's text location search: the browser never
 * calls apps/api directly (ADR-004), so this Route Handler is the one
 * place a Client Component's fetch can land. It forwards `?q=` to
 * apps/api's `GET /v1/locations/search` through the signed internal
 * path (lib/internal-api-client.ts) and returns the result as plain
 * JSON -- no session to validate yet (no Better Auth, sprint 28), so
 * this is intentionally an anonymous, public search, matching the
 * product's pre-signup browsing flow.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const q = request.nextUrl.searchParams.get('q')?.trim()
  if (!q) {
    return NextResponse.json({ error: 'q is required' }, { status: 400 })
  }

  try {
    const results = await internalApiFetch<LocationSearchResult[]>(
      `/v1/locations/search?q=${encodeURIComponent(q)}`,
    )
    return NextResponse.json(results)
  } catch {
    return NextResponse.json(
      { error: 'location search is temporarily unavailable' },
      { status: 502 },
    )
  }
}
