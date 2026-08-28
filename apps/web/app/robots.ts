import type { MetadataRoute } from 'next'

/**
 * Sprint 49 ("SEO and sharing"): every page in `apps/web` is public
 * today (no auth, sprint 28) -- there's nothing to disallow yet.
 * `docs/CANONICAL_ROADMAP.md`'s "private dashboards" half of this
 * sprint's acceptance bar (a real `Disallow` rule for authenticated
 * routes) is a follow-up for when those routes exist. No `sitemap`
 * directive is emitted: a real sitemap needs an endpoint that can
 * enumerate every curated location (apps/api's 101-spot dataset isn't
 * exposed that way today, only via `/v1/locations/search`'s query-based
 * lookup) -- inventing a partial sitemap from whatever happens to be in
 * the search cache would misrepresent the site's real page count more
 * than omitting it entirely.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: '*',
      allow: '/',
    },
  }
}
