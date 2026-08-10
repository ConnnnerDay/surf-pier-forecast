/** Best-effort "Browser on OS" label from the UA string, used only to let
 * the backend recognize "you've signed in from this device before" for the
 * new-device login-alert email — not meant to be a precise UA parse. */
export function deviceLabel(): string {
  const ua = navigator.userAgent

  let browser = 'Unknown browser'
  if (ua.includes('Edg/')) browser = 'Edge'
  else if (ua.includes('Chrome/')) browser = 'Chrome'
  else if (ua.includes('Firefox/')) browser = 'Firefox'
  else if (ua.includes('Safari/')) browser = 'Safari'

  let os = 'unknown OS'
  if (ua.includes('iPhone') || ua.includes('iPad')) os = 'iOS'
  else if (ua.includes('Android')) os = 'Android'
  else if (ua.includes('Mac OS X')) os = 'macOS'
  else if (ua.includes('Windows')) os = 'Windows'
  else if (ua.includes('Linux')) os = 'Linux'

  return `${browser} on ${os}`
}
