# OpenMontage production preflight

This adapter safely prepares short-form production proposals against the exact pinned OpenMontage manifests. It fingerprints user-confirmed source media, records a rights basis, caps the budget, and surfaces every upstream human-approval gate.

The preflight makes no network calls, installs no provider dependencies, and cannot render or spend money. Approval is a distinct confirmed action and remains non-executable until a later runtime adapter adds dependency isolation, cost reconciliation, provenance capture, and per-provider authorization.
