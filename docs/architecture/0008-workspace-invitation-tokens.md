# ADR 0008: Workspace invitation token lifecycle

## Decision

Workspace owners can issue role-bound invitations for a normalized email address. Each invitation expires within one to 168 hours (72 hours by default), can be revoked before use, and can be accepted only by an authenticated account whose email matches exactly after normalization.

The API generates a cryptographically random token and returns it only in the creation response. The database stores only its SHA-256 digest. Acceptance is single-use, creates the membership and audit event in the same transaction, and rejects expired, revoked, replayed, mismatched-email, and already-member attempts.

## Consequences

The web UI always returns a copyable one-time invitation link and can optionally deliver it through configured SMTP. The token digest and creation audit commit before the external send, ensuring a delivered link is valid. The raw token exists only in request memory, the one-time response, and the outgoing message; it is never placed in SQL jobs, audit details, or logs.

SMTP permits STARTTLS or implicit TLS only, and invitation links require HTTPS except on loopback during development. Delivery is owner-only, bounded per workspace per hour, and records metadata-only attempt and sent/failed audit events. Provider failure does not invalidate or hide the copyable link. Delivery is intentionally not retried durably because doing so would require persisting recoverable raw-token material. Invitation records expose status and metadata but never the raw token.
