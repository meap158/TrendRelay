# mvanhorn/last30days-skill

- Repository: https://github.com/mvanhorn/last30days-skill
- Pinned revision: `249c7a4c040558a903d6838dee31012980d4946d`
- Audited version: 3.16.0
- License: MIT
- TrendRelay role: recent trend and audience research
- Local source: `.tools/catalog/last30days-skill/source`

The project searches and synthesizes recent discussion across sources including Reddit, X, YouTube, Hacker News, Polymarket, GitHub, TikTok, and the web. Python 3.12+ is required. Many sources work without credentials; others use optional API keys or browser-derived credentials.

TrendRelay installs only the pinned source checkout and never globally installs an agent skill. The native adapter executes its stable agent JSON 1.x contract, disables browser-cookie extraction, passes only allowlisted research secrets, and ingests results as workspace-scoped evidence under `.data/research/last30days/`. Live runs require installation, activation, and explicit external-action confirmation.
