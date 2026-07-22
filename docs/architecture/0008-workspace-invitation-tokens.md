# ADR 0008: Workspace invitation token lifecycle

## Decision

Workspace owners can issue role-bound invitations for a normalized email address. Each invitation expires within one to 168 hours (72 hours by default), can be revoked before use, and can be accepted only by an authenticated account whose email matches exactly after normalization.

The API generates a cryptographically random token and returns it only in the creation response. The database stores only its SHA-256 digest. Acceptance is single-use, creates the membership and audit event in the same transaction, and rejects expired, revoked, replayed, mismatched-email, and already-member attempts.

## Consequences

The web UI can copy a one-time invitation link for delivery through a trusted channel. Automated transactional-email delivery is intentionally not part of this boundary; adding it requires an approved provider, a secret reference, delivery telemetry, and rate limits. Invitation records expose status and metadata but never the raw token.
