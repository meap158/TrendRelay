# mvanhorn/last30days-skill

- Repository: https://github.com/mvanhorn/last30days-skill
- Pinned revision: `249c7a4c040558a903d6838dee31012980d4946d`
- Audited version: 3.3.0
- License: MIT
- TrendRelay role: recent trend and audience research
- Local source: `.tools/catalog/last30days-skill/source`

The project searches and synthesizes recent discussion across sources including Reddit, X, YouTube, Hacker News, Polymarket, GitHub, TikTok, and the web. Python 3.12+ is required. Many sources work without credentials; others use optional API keys or browser-derived credentials.

TrendRelay installs only the pinned source checkout. Activation marks it eligible for future research orchestration; it does not globally install an agent skill or copy credentials. The TrendRelay-native job adapter, scoped secret mapping, result schema, and evidence ingestion remain pending.
