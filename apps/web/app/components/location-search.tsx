'use client'

import { useEffect, useId, useRef, useState } from 'react'
import { Card, Field } from './ui'
import { cx } from './ui/cx'
import type { LocationSearchResult } from '@/app/api/locations/search/route'

const DEBOUNCE_MS = 300
const MIN_QUERY_LENGTH = 2

export type LocationSearchProps = {
  onSelect: (location: LocationSearchResult) => void
  label?: string
  placeholder?: string
}

/**
 * Sprint 31's text location search (candidate acceptance sub-item;
 * device geolocation, map search, and station-preview/ambiguity states
 * are deliberately not attempted here -- each is its own sizeable UI,
 * and text search alone is already a complete, useful capability).
 * Calls the BFF's `/api/locations/search` Route Handler, never
 * apps/api directly (ADR-004 -- the signing secret is server-only).
 *
 * Implements the WAI-ARIA combobox pattern by hand (no dependency):
 * `role="combobox"` on the input, `role="listbox"` on the results,
 * `aria-activedescendant` tracks keyboard-selected option, Escape
 * closes, Enter/click selects. See
 * https://www.w3.org/WAI/ARIA/apg/patterns/combobox/ for the pattern
 * this follows.
 */
export function LocationSearch({
  onSelect,
  label = 'Search for a location',
  placeholder = 'e.g. Wrightsville Beach',
}: LocationSearchProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<LocationSearchResult[]>([])
  const [isOpen, setIsOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [error, setError] = useState<string | null>(null)
  const listboxId = useId()
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const containerRef = useRef<HTMLDivElement>(null)
  const skipNextSearchRef = useRef(false)

  useEffect(() => {
    function handleOutsideClick(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleOutsideClick)
    return () => document.removeEventListener('mousedown', handleOutsideClick)
  }, [])

  useEffect(() => {
    clearTimeout(debounceRef.current)

    // `selectResult` below sets `query` to the chosen result's full name
    // to fill the field, which -- since that name still meets
    // MIN_QUERY_LENGTH -- would otherwise re-trigger this same search
    // effect and reopen the dropdown ~DEBOUNCE_MS after a selection,
    // found via a keyboard-only walkthrough (arrow to a result, Enter,
    // then watch aria-expanded flip back to true on its own). Selecting
    // a result is not a query edit, so it skips search exactly once.
    if (skipNextSearchRef.current) {
      skipNextSearchRef.current = false
      return
    }

    if (query.trim().length < MIN_QUERY_LENGTH) {
      setResults([])
      setIsOpen(false)
      setError(null)
      return
    }

    debounceRef.current = setTimeout(async () => {
      try {
        const response = await fetch(
          `/api/locations/search?q=${encodeURIComponent(query.trim())}`,
        )
        if (!response.ok) {
          throw new Error('search failed')
        }
        const data = (await response.json()) as LocationSearchResult[]
        setResults(data)
        setIsOpen(true)
        setActiveIndex(-1)
        setError(null)
      } catch {
        setError('Search is temporarily unavailable.')
        setResults([])
      }
    }, DEBOUNCE_MS)

    return () => clearTimeout(debounceRef.current)
  }, [query])

  function selectResult(result: LocationSearchResult) {
    onSelect(result)
    skipNextSearchRef.current = true
    setQuery(result.name)
    setIsOpen(false)
    setActiveIndex(-1)
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!isOpen || results.length === 0) return

    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex((index) => (index + 1) % results.length)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex((index) => (index <= 0 ? results.length - 1 : index - 1))
    } else if (event.key === 'Enter' && activeIndex >= 0) {
      event.preventDefault()
      selectResult(results[activeIndex])
    } else if (event.key === 'Escape') {
      setIsOpen(false)
      setActiveIndex(-1)
    }
  }

  const activeOptionId =
    activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined
  const showNoResults = isOpen && results.length === 0 && !error
  // ARIA's combobox role requires `aria-controls` to be present at all
  // times (axe-core's aria-required-attr) *and* to reference an element
  // that actually exists in the DOM (aria-valid-attr-value). Those two
  // rules are only satisfiable together if the listbox element is always
  // rendered -- so, unlike the first fix attempt here (which only
  // existed when there were results, and left aria-controls dangling in
  // the zero-results state), the listbox below is now unconditional and
  // its visibility is toggled with the native `hidden` attribute, the
  // convention the ARIA APG combobox pattern itself uses
  // (https://www.w3.org/WAI/ARIA/apg/patterns/combobox/). `hidden` also
  // removes it (and the empty-listbox case) from axe's accessibility-tree
  // checks while closed, which sidesteps aria-required-children on a
  // listbox with no option children in the idle state.
  const listboxHasContent = (isOpen && results.length > 0) || showNoResults

  return (
    <div className="relative" ref={containerRef}>
      <Field
        label={label}
        placeholder={placeholder}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={() => results.length > 0 && setIsOpen(true)}
        role="combobox"
        aria-expanded={listboxHasContent}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={activeOptionId}
        autoComplete="off"
        error={error ?? undefined}
      />

      <Card className="absolute z-10 mt-1 w-full p-0" hidden={!listboxHasContent}>
        {/* role="listbox"/"option" on plain divs, not ul/li: overriding
            li's implicit "listitem" role to "option" breaks the ARIA
            required-owned-elements relationship a real <ul> expects
            from its children (axe-core's aria-required-children/
            aria-required-parent/list rules all catch this) -- divs
            carry no conflicting implicit role. */}
        <div
          id={listboxId}
          role="listbox"
          className="max-h-64 divide-y divide-border overflow-y-auto py-1"
        >
          {results.length > 0
            ? results.map((result, index) => (
                <div
                  key={result.id}
                  id={`${listboxId}-option-${index}`}
                  role="option"
                  aria-selected={index === activeIndex}
                  className={cx(
                    'flex cursor-pointer items-center justify-between gap-2 px-3 py-2.5 text-sm text-text',
                    index === activeIndex && 'bg-primary-tint',
                  )}
                  onMouseDown={(event) => {
                    event.preventDefault()
                    selectResult(result)
                  }}
                  onMouseEnter={() => setActiveIndex(index)}
                >
                  <span>
                    {result.name}, {result.state}
                  </span>
                  {index === activeIndex && (
                    <svg
                      width="15"
                      height="15"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      className="shrink-0 text-primary"
                      strokeWidth="2.4"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M5 13 L10 18 L20 6" />
                    </svg>
                  )}
                </div>
              ))
            : showNoResults && (
                // A non-selectable option row, not a plain <p>, so the
                // listbox still has a valid owned "option" child while
                // it's open with zero matches -- an empty role="listbox"
                // here would re-trigger aria-required-children.
                <div
                  role="option"
                  aria-disabled="true"
                  aria-selected="false"
                  className="px-3 py-2 text-sm text-text-muted"
                >
                  No matches for &ldquo;{query.trim()}&rdquo;.
                </div>
              )}
        </div>
      </Card>
    </div>
  )
}
