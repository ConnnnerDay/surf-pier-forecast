'use client'

import { useState } from 'react'
import { Card, Container } from '@/app/components/ui'
import { LocationSearch } from '@/app/components/location-search'
import type { LocationSearchResult } from '@/app/api/locations/search/route'

/**
 * Sprint 31 (text location search only -- see LocationSearch's
 * docstring for what's deliberately not attempted). Demonstrates the
 * search capability standalone; wiring a selected location into a real
 * forecast lookup (replacing app/forecast/demo's fixed location) is the
 * natural next step, not done here to keep this change focused.
 */
export default function LocationsPage() {
  const [selected, setSelected] = useState<LocationSearchResult | null>(null)

  return (
    <main>
      <Container>
        <header className="py-10 sm:py-16">
          <p className="text-sm font-medium tracking-wide text-primary uppercase">
            Sprint 31 (partial) — text search only
          </p>
          <h1 className="mt-2 text-3xl font-bold text-text sm:text-4xl">
            Find a location
          </h1>
          <p className="mt-3 max-w-prose text-text-muted">
            Search apps/api&apos;s curated coastal locations through the
            signed BFF path — device geolocation, map search, and
            station-preview states aren&apos;t built yet.
          </p>
        </header>

        <div className="max-w-sm pb-16">
          <LocationSearch onSelect={setSelected} />

          {selected && (
            <Card className="mt-6">
              <h2 className="font-semibold text-text">{selected.name}</h2>
              <p className="mt-1 text-sm text-text-muted">
                {selected.state} · {selected.lat.toFixed(3)}, {selected.lng.toFixed(3)}
              </p>
              <p className="mt-1 text-sm text-text-muted">
                <code>{selected.id}</code>
              </p>
            </Card>
          )}
        </div>
      </Container>
    </main>
  )
}
