# Last 30 Days research plugin

This adapter executes the exact pinned `mvanhorn/last30days-skill` revision through its stable agent JSON 1.x contract. TrendRelay converts ranked results into workspace-scoped observations and preserves the original evidence record in `.data/research/last30days/`.

The subprocess receives only operating-system variables needed to run plus the secret names allowlisted in the manifest. Browser-cookie extraction is disabled. A live run requires explicit confirmation and an installed, active provider; mock runs are available for deterministic local verification.
