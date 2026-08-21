import { randomUUID } from 'node:crypto'
import { sha256Hex, sign, type SignedRequestFields } from './internal-signature'

/**
 * Server-only client for calling apps/api through ADR-004's signed
 * internal request path (docs/architecture.md). Never call this from a
 * Client Component -- it reads server-only env vars and signs with a
 * secret that must never reach the browser bundle.
 *
 * Env vars (see apps/web/README.md and apps/api/README.md -- both apps
 * need the *same* INTERNAL_SIGNING_KEY_ID/SECRET values for local dev):
 * - INTERNAL_API_BASE_URL: apps/api's base URL, default
 *   http://localhost:8000
 * - INTERNAL_SIGNING_KEY_ID / INTERNAL_SIGNING_KEY_SECRET: required,
 *   throws if either is unset -- fails closed, matching apps/api's
 *   InternalAuthDependency posture, rather than silently calling
 *   unsigned and letting apps/api's own 401 surface as a confusing
 *   downstream error.
 *
 * `userId` is always omitted for now: there's no Better Auth (sprint 28)
 * session to source a real one from yet.
 */

const VALIDITY_SECONDS = 20

class InternalApiError extends Error {
  constructor(
    public status: number,
    public bodyText: string,
  ) {
    super(`internal API request failed: ${status} ${bodyText}`)
  }
}

function requiredEnv(name: string): string {
  const value = process.env[name]
  if (!value) {
    throw new Error(
      `${name} is not set -- required to sign requests to apps/api (see apps/web/README.md)`,
    )
  }
  return value
}

export async function internalApiFetch<T>(
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  const baseUrl = process.env.INTERNAL_API_BASE_URL ?? 'http://localhost:8000'
  const keyId = requiredEnv('INTERNAL_SIGNING_KEY_ID')
  const secret = requiredEnv('INTERNAL_SIGNING_KEY_SECRET')

  const method = init.method ?? 'GET'
  const bodyText = init.body !== undefined ? JSON.stringify(init.body) : ''
  const now = Math.floor(Date.now() / 1000)

  const fields: SignedRequestFields = {
    method,
    path,
    bodyDigest: sha256Hex(bodyText),
    userId: '',
    issuedAt: now,
    expiresAt: now + VALIDITY_SECONDS,
    requestId: randomUUID(),
    keyId,
  }

  const headers: Record<string, string> = {
    'X-Internal-Key-Id': keyId,
    'X-Internal-Request-Id': fields.requestId,
    'X-Internal-Issued-At': String(fields.issuedAt),
    'X-Internal-Expires-At': String(fields.expiresAt),
    'X-Internal-Signature': sign(secret, fields),
  }
  if (bodyText) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers,
    body: bodyText || undefined,
    cache: 'no-store',
  })

  if (!response.ok) {
    throw new InternalApiError(response.status, await response.text())
  }

  return (await response.json()) as T
}
