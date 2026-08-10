/** Browser <-> backend format conversion for WebAuthn. The backend sends/
 * expects base64url strings (see webauthn.helpers.bytes_to_base64url on the
 * Python side); the browser's navigator.credentials API works in
 * ArrayBuffers. This file is the boilerplate that bridges the two. */

function base64urlToBuffer(base64url: string): ArrayBuffer {
  const padded = base64url.padEnd(base64url.length + ((4 - (base64url.length % 4)) % 4), '=')
  const base64 = padded.replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

function bufferToBase64url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

interface DescriptorJSON {
  id: string
  type: string
  transports?: string[]
}

function decodeDescriptors(
  list: DescriptorJSON[] | undefined,
): PublicKeyCredentialDescriptor[] | undefined {
  return list?.map((d) => ({
    id: base64urlToBuffer(d.id),
    type: 'public-key',
    transports: d.transports as AuthenticatorTransport[] | undefined,
  }))
}

/** Runs navigator.credentials.create() from the backend's JSON registration
 * options, returning a JSON-safe credential ready to POST to
 * /auth/passkey/register/verify. */
export async function createPasskey(optionsJSON: any): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...optionsJSON,
    challenge: base64urlToBuffer(optionsJSON.challenge),
    user: { ...optionsJSON.user, id: base64urlToBuffer(optionsJSON.user.id) },
    excludeCredentials: decodeDescriptors(optionsJSON.excludeCredentials),
  }

  const credential = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential
  const response = credential.response as AuthenticatorAttestationResponse

  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
      attestationObject: bufferToBase64url(response.attestationObject),
    },
  }
}

/** Runs navigator.credentials.get() from the backend's JSON authentication
 * options, returning a JSON-safe credential ready to POST to
 * /auth/passkey/login/verify. */
export async function getPasskey(optionsJSON: any): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...optionsJSON,
    challenge: base64urlToBuffer(optionsJSON.challenge),
    allowCredentials: decodeDescriptors(optionsJSON.allowCredentials),
  }

  const credential = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential
  const response = credential.response as AuthenticatorAssertionResponse

  return {
    id: credential.id,
    rawId: bufferToBase64url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: bufferToBase64url(response.clientDataJSON),
      authenticatorData: bufferToBase64url(response.authenticatorData),
      signature: bufferToBase64url(response.signature),
      userHandle: response.userHandle ? bufferToBase64url(response.userHandle) : undefined,
    },
  }
}

export function passkeysSupported(): boolean {
  return typeof window !== 'undefined' && !!window.PublicKeyCredential
}
