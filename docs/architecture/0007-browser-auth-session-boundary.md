# ADR 0007: Browser authentication session boundary

## Decision

The web application uses the official Supabase JavaScript client with PKCE, automatic token refresh, and persisted browser sessions. UI components receive the signed-in user and an `apiFetch` capability; they do not receive or render access or refresh tokens. The FastAPI boundary remains authoritative and verifies every bearer token against asymmetric JWKS.

Browser flows include password sign-in and account creation, email verification redirects, magic links, Google OAuth, password recovery, and global sign-out. Google OAuth initiated inside Electron uses `skipBrowserRedirect` and opens the authorization URL through Electron's existing external-window handler, keeping OAuth out of the embedded webview.

## Consequences

The browser can manage workspaces, members, secret references, and audit events after a Supabase project is configured. Electron device pairing is still required to return a system-browser identity to the desktop securely; until then, the authenticated browser session and Electron session are separate.

References: [Supabase browser client](https://supabase.com/docs/reference/javascript/auth), [password sign-in](https://supabase.com/docs/reference/javascript/auth-signinwithpassword), and [auth state changes](https://supabase.com/docs/reference/javascript/auth-onauthstatechange).
