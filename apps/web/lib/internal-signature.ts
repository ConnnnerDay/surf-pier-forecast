import { createHash, createHmac } from 'node:crypto'

/**
 * TypeScript mirror of apps/api/app/infra/internal_signature.py's pure
 * verification primitive -- same canonical-string field order, same
 * delimiter, same HMAC-SHA-256 algorithm. Field order here MUST match
 * that module exactly, since it's the entire signing contract (ADR-004,
 * docs/architecture.md). Server-only: only ever import this from a
 * Server Component or Route Handler, never a Client Component -- Next.js
 * doesn't enforce that automatically without the `server-only` package,
 * which this repo doesn't depend on yet, so it's a convention, not a
 * guarantee.
 */

export type SignedRequestFields = {
  method: string
  path: string
  bodyDigest: string
  userId: string
  issuedAt: number
  expiresAt: number
  requestId: string
  keyId: string
}

export function sha256Hex(body: string | Buffer): string {
  return createHash('sha256').update(body).digest('hex')
}

function canonicalString(fields: SignedRequestFields): string {
  return [
    fields.method.toUpperCase(),
    fields.path,
    fields.bodyDigest,
    fields.userId,
    String(fields.issuedAt),
    String(fields.expiresAt),
    fields.requestId,
    fields.keyId,
  ].join('\n')
}

export function sign(secret: string, fields: SignedRequestFields): string {
  return createHmac('sha256', secret).update(canonicalString(fields)).digest('hex')
}
