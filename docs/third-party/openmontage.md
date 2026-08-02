# calesthio/OpenMontage

- Repository: https://github.com/calesthio/OpenMontage
- Pinned revision: `af87fc1337254ec1978e6333b5acbbb5ffb9a3d0`
- License: GNU AGPL v3
- TrendRelay role: governed video preflight and deterministic local clipping
- Local source: `.tools/catalog/openmontage/source`

OpenMontage provides agentic research, scripting, asset generation, editing, composition, review gates, and rendering pipelines. Its full setup includes optional media/model providers that use separate API keys and may incur cost. TrendRelay does not run that setup or authorize those providers.

TrendRelay exposes the pinned `clip-factory` and `podcast-repurpose` manifests for immutable-source, rights, budget, and human approval preflights. After approval, the local runtime can invoke the upstream `VideoTrimmer` implementation for explicit manual clip ranges. It uses locked FFmpeg/ffprobe packages, re-encodes keyframe-safe MP4 clips, verifies video streams and duration, hashes every artifact, and records zero actual provider cost plus upstream/binary provenance. Outputs stay under ignored `.data/productions/openmontage/`.

The subprocess receives a scrubbed environment containing no provider credentials and declares no network requirement. A compatibility shim deliberately prevents OpenMontage's base module from auto-loading its `.env`. Source hashes are checked at submission and execution. Rendering never publishes media; the publishing engine remains a separate approval and external-action boundary.

Provider-backed generation, automatic editorial decisions, and the rest of the upstream agentic pipeline remain disabled until scoped authorization, cost reservation/reconciliation, provider-specific provenance, and separate license review are implemented. See `docs/third-party/ffmpeg-static.md` for the GPL posture of the packaged media binaries.
