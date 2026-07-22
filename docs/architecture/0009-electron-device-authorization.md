# ADR 0009: Electron device authorization grant

## Decision

TrendRelay Desktop uses an OAuth-style device flow instead of transferring a Supabase browser session into Electron. The desktop starts a pairing from loopback and receives a high-entropy device code plus a separate eight-character approval code. The user reviews the device name in an authenticated system browser and explicitly approves it within ten minutes.

The database stores only the SHA-256 digest of the device code. A successful one-time exchange produces an eight-hour TrendRelay device JWT containing the user, device pairing ID, issuer, audience, issue time, and expiry. Device JWTs use a distinct token type and HS256 key; the API never accepts HS256 for Supabase tokens. Local development generates the signing secret under ignored `.data/`, while production requires `DEVICE_TOKEN_SECRET`.

## Consequences

No Supabase access or refresh token crosses the browser-to-desktop boundary. Pairing requests can start and exchange only over loopback, approval requires an authenticated browser account, and replayed or expired grants fail closed. The remaining Electron IPC broker must keep the resulting app token in the main process, encrypt it with Electron `safeStorage`, and expose only an authorized-request capability to the renderer.
