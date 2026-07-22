# calesthio/OpenMontage

- Repository: https://github.com/calesthio/OpenMontage
- Pinned revision: `af87fc1337254ec1978e6333b5acbbb5ffb9a3d0`
- License: GNU AGPL v3
- TrendRelay role: video production and long-to-short repurposing
- Local source: `.tools/catalog/openmontage/source`

OpenMontage provides agentic research, scripting, asset generation, editing, composition, review gates, and rendering pipelines. Its prerequisites include Python, Node.js, and FFmpeg; optional media/model providers use separate API keys and may incur cost. Its clip-factory and social-video pipelines overlap strongly with TrendRelay's planned creative worker.

TrendRelay installs only the pinned source checkout and does not run `make setup`, install provider dependencies, send prompts, consume paid APIs, or create media. The native preflight adapter supports the `clip-factory` and `podcast-repurpose` manifests: it fingerprints confirmed source media, records its rights basis, caps budget, and surfaces upstream approval gates. Proposal approval remains non-executable until a future runtime adapter adds isolated dependencies, cost reconciliation, output provenance, and AGPL-compliant production operations.
