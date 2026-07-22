# ADR 0007: Browser authentication session boundary

## Decision

The web application uses the official Supabase JavaScript client with PKCE, automatic token refresh, and persisted browser sessions. UI components receive the signed-in user and an `apiFetch` capability; they do not receive or render access or refresh tokens. The FastAPI boundary remains authoritative and verifies every bearer token against asymmetric JWKS.

Browser flows include password sign-in and account creation, email verification redirects, magic links, Google OAuth, password recovery, global sign-out, and optional TOTP authenticator enrollment. An enrolled session at AAL1 is globally redirected to a challenge before authenticated application screens render; successful verification refreshes the session at AAL2. Unverified enrollment factors can be discarded, while removing a verified factor requires AAL2. Google OAuth initiated inside Electron uses `skipBrowserRedirect` and opens the authorization URL through Electron's existing external-window handler, keeping OAuth out of the embedded webview.

## Consequences

The browser can manage workspaces, members, secret references, and audit events after a Supabase project is configured. `REQUIRE_AAL2_FOR_GOVERNED_ACTIONS` optionally makes the FastAPI boundary enforce the signed `aal2` claim for sensitive owner, publishing, and device-approval actions; missing or unknown assurance claims are treated as AAL1. Electron device pairing returns a distinct app token rather than transferring the Supabase session.

References: [Supabase browser client](https://supabase.com/docs/reference/javascript/auth), [password sign-in](https://supabase.com/docs/reference/javascript/auth-signinwithpassword), [auth state changes](https://supabase.com/docs/reference/javascript/auth-onauthstatechange), and [TOTP MFA](https://supabase.com/docs/guides/auth/auth-mfa/totp).
