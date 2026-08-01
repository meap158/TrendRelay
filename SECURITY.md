# Security policy

## Reporting a vulnerability

Please do not disclose security vulnerabilities in a public issue. Use the repository's **Security → Report a vulnerability** flow to open a private GitHub security advisory with:

- the affected component and version or commit;
- reproduction steps or a minimal proof of concept;
- the expected impact; and
- any suggested mitigation.

Do not include real access tokens, cookies, downloaded media, personal data, or third-party credentials. Replace them with clearly marked test values.

## Supported version

Until TrendRelay publishes versioned releases, security fixes target the latest commit on `main`.

## Security boundaries

TrendRelay keeps local media, provider checkouts, cookies, databases, generated device secrets, and other runtime state under ignored `.data/` and `.tools/` directories. Local authentication bypass is limited to loopback development and must be disabled for shared or production deployments.
