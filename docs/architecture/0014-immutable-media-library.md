# ADR 0014: Store immutable media and govern reuse by rights

Status: Accepted

## Context

TrendRelay downloads reference media and creates production artifacts, but filesystem paths alone do not provide durable identity, provenance, reusable creative intelligence, or a safe publication boundary. The product concept requires immutable originals, deduplication, derivatives, transcripts, OCR, scene analysis, rights classification, and search. Reference media must remain useful for research without silently becoming publishable.

## Decision

- A workspace owns `MediaAsset` records identified by the SHA-256 digest of the original bytes. A second import of identical bytes is idempotent.
- Originals are copied once into a hash-addressed directory beneath `.data/media/` and are never edited. FFmpeg creates separate thumbnail, 720p proxy, and mono 16 kHz audio versions; every version receives its own digest and metadata.
- Ingestion uses the leased SQL job queue. The unified worker verifies that source bytes have not changed between submission and execution.
- Douyin downloads enter the library as `unknown`. Governed OpenMontage artifacts inherit only the explicitly recorded `owned`, `licensed`, or `public-domain` classification; all other cases remain `unknown`.
- Rights are one of `owned`, `licensed`, `public-domain`, `unknown`, or `prohibited`. Only the first three are publishable. Rights changes require an owner or approver, governed assurance, explicit confirmation, a written basis, and an audit event.
- Campaign planning hashes the selected file and rejects it when the bytes match a known non-publishable original or derivative. Copying or renaming a file cannot bypass the rights gate.
- Reviewed speech and OCR text are stored separately from versioned creative analysis. TrendRelay derives hooks, calls to action, products, formats, structures, scene pacing, reveal timing, caption density, and keywords without presenting unreviewed machine output as fact.
- Automatic transcription remains unavailable until a reviewed local provider is incorporated. The API and UI report that limitation directly and accept operator-reviewed transcript/OCR text today.
- Content responses remain authenticated. Browser previews use authorized blob requests rather than public filesystem URLs.

## Consequences

The Media Library becomes the trusted bridge between acquisition, research, production, and campaigns. It uses additional disk space for immutable originals and derivatives, but the hash-addressed layout makes provenance and deduplication explicit. Shared deployments will later move version bytes to S3-compatible storage while retaining these database contracts.

Search is currently database-backed and suitable for the local first release. Full-text/vector indexing and automated transcription/OCR may be added behind provider boundaries without changing asset identity or weakening the rights gate.
