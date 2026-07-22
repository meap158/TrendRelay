# ADR 0006: Authenticated workspace foundation

## Decision

TrendRelay accepts Supabase Auth access tokens at the Python API boundary and verifies asymmetric `RS256` or `ES256` signatures against the project JWKS. Issuer, audience, expiry, and subject are mandatory; shared signing secrets and renderer-held service credentials are not supported.

Workspace data uses SQLAlchemy 2 and Alembic. SQLite is the zero-setup local database, while PostgreSQL remains the shared and production target. Every governed record starts with a `workspace_id`; initial roles are owner, editor, approver, and analyst.

Secret records contain only an approved locator (`os-keyring://`, `vault://`, `supabase-vault://`, or `env://`). Raw secret values are rejected. Workspace creation, member addition, and secret-reference creation append audit events in the same transaction.

## Consequences

The API foundation is ready for a Supabase sign-in UI and workspace-aware feature migration. A configured Supabase project is required for real requests; tests override the verified identity dependency. Research and production JSON persistence is not migrated by this decision.

References: [Supabase JWT verification](https://supabase.com/docs/guides/auth/jwts), [SQLAlchemy session transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html), and [Alembic](https://alembic.sqlalchemy.org/).
