# Infrastructure

TrendRelay uses managed services or locally installed equivalents for PostgreSQL with pgvector, Redis, and S3-compatible object storage. Provisioning modules belong in `infra/terraform/` once an environment is selected.

Local application development does not require an infrastructure runtime until a feature depends on one of these services.
