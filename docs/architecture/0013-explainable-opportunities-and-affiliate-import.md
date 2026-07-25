# ADR 0013: Explainable opportunities and affiliate import

Status: Accepted
Date: 2026-07-26

## Context

TrendRelay must connect trend evidence to monetizable offers before content production. The product concept requires product and affiliate-link import, opportunity cards, an explainable score, and campaign creation from an opportunity. Affiliate APIs are not universally available, and a single opaque AI score would not be auditable.

## Decision

- Store canonical workspace-scoped products separately from network-specific offers.
- Make UTF-8 CSV import the first universal connector. Required columns are `product_name`, `marketplace`, `network`, and `affiliate_url`; pricing, commission, cookie window, availability, restrictions, and product metadata are optional.
- Deduplicate products and offers with deterministic workspace-scoped fingerprints. Re-importing the same feed is idempotent.
- Persist evidence, operator inputs, score version, and every weighted contribution with its reason and evidence identifiers.
- Use deterministic scoring version `v1`:
  - positive factors follow the product-concept weights;
  - cross-platform confirmation derives from distinct evidence sources;
  - affiliate economics derives from usable offer count and percentage commission;
  - competition and policy risk are explicit negative contributions;
  - the final score is clamped to 0–100.
- A completed research job can supply normalized evidence only when its workspace matches the opportunity workspace.
- “Create Campaign” copies markets, languages, the primary affiliate URL, and score context into a draft campaign, while a first-party link table retains opportunity and offer provenance.

## Consequences

Operators can inspect why a score moved, import offers without waiting for network credentials, and trace a campaign back to evidence and economics. Future Amazon Creators, impact.com, or Awin adapters should write the same canonical product and offer records. Score changes require a new explicit version and recomputation path; version `v1` records remain reproducible.
