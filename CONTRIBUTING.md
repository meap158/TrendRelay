# Contributing to TrendRelay

Thanks for helping improve TrendRelay. Keep changes focused, preserve provider and rights boundaries, and include tests for behavior that can affect media provenance, publishing, authentication, or durable jobs.

## Development setup

1. Install Node.js 22+, Python 3.12+, and Git.
2. Run `npm ci`.
3. Create a virtual environment and install `services/api[dev]`.
4. Copy `.env.example` to `.env` only when you need local overrides.
5. Start with `npm run dev` or, on Windows, `start.cmd`.

## Before opening a pull request

Run:

```powershell
npm run release:check
```

Never commit `.env`, `.data/`, `.tools/`, downloaded media, provider cookies, databases, build output, or real credentials. Document newly incorporated third-party projects in `docs/third-party/` and pin reviewed revisions in `config/tool-catalog.json`.

## Pull requests

- Explain the user-visible outcome and important tradeoffs.
- Link relevant issues or architecture decisions.
- Include screenshots for interface changes.
- Keep unrelated formatting or generated-file churn out of the change.
- Confirm that new collection, download, production, or publishing behavior respects platform terms and content rights.
