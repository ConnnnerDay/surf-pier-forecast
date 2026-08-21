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
        aria-expanded={isOpen}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={activeOptionId}
        autoComplete="off"
        error={error ?? undefined}
      />

      {isOpen && results.length === 0 && !error && (
        <p className="mt-1 text-sm text-text-muted">
          No matches for &ldquo;{query.trim()}&rdquo;.
        </p>
      )}

      {isOpen && results.length > 0 && (
        <Card
          className="absolute z-10 mt-1 w-full p-0"
          id={listboxId}
          role="listbox"
        >
          <ul className="max-h-64 overflow-y-auto py-1">
            {results.map((result, index) => (
              <li
                key={result.id}
                id={`${listboxId}-option-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                className={cx(
                  'cursor-pointer px-3 py-2 text-sm',
                  index === activeIndex ? 'bg-primary text-primary-contrast' : 'text-text',
                )}
                onMouseDown={(event) => {
                  event.preventDefault()
                  selectResult(result)
                }}
                onMouseEnter={() => setActiveIndex(index)}
              >
                {result.name}, {result.state}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}
