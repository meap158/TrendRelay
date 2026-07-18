# calesthio/OpenMontage

- Repository: https://github.com/calesthio/OpenMontage
- Pinned revision: `af87fc1337254ec1978e6333b5acbbb5ffb9a3d0`
- License: GNU AGPL v3
- TrendRelay role: video production and long-to-short repurposing
- Local source: `.tools/catalog/openmontage/source`

OpenMontage provides agentic research, scripting, asset generation, editing, composition, review gates, and rendering pipelines. Its prerequisites include Python, Node.js, and FFmpeg; optional media/model providers use separate API keys and may incur cost. Its clip-factory and social-video pipelines overlap strongly with TrendRelay's planned creative worker.

TrendRelay installs only the pinned source checkout. Activation does not run `make setup`, install Python/npm dependencies, send prompts, consume paid APIs, or create media. A future adapter must isolate its environment, map approved assets, enforce cost and approval gates, capture provenance, and satisfy AGPL obligations before production use.
