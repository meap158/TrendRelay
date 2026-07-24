# TheMattBerman/meta-ads-kit

- Repository: https://github.com/TheMattBerman/meta-ads-kit
- Pinned revision: `0879bb4566a836670f33beb509ff7d8d4779849e`
- License: MIT
- TrendRelay role: first-party Meta Ads performance research and creative validation
- Local source: `.tools/catalog/meta-ads-kit/source`
- Isolated runtime: `.tools/catalog/meta-ads-kit/runtime`

Meta Ads Kit turns campaign data into a short briefing covering spend, active campaigns, winners, bleeders, and creative-fatigue signals. It complements topic research: Last 30 Days discovers recent demand, Agent Reach reports available channel routes, and Meta Ads Kit validates ideas against the operator's own ad performance.

TrendRelay installs the exact source revision plus `@vishalgojha/social-flow` 0.2.17 in the tool-local runtime. The upstream revision references the former `@vishalgojha/social-cli` package; npm has renamed that package to Social Flow while retaining the `social` command. No global package is required.

The native Python adapter calls only read operations: account status, active campaigns, campaign insights, ad insights, and daily fatigue fields. It does not expose pause, resume, budget, creation, upload, or deletion commands. The API is loopback-only and requires confirmation for every briefing. CLI output errors are sanitized, token values are never returned, and no campaign mutation is possible through this adapter.

Install or verify:

```powershell
npm run tools -- install meta-ads-kit --confirm-external-action
npm run tools -- activate meta-ads-kit
npm run meta-ads -- check
```

Authentication remains an explicit account-holder action through the isolated executable: `.\.tools\catalog\meta-ads-kit\runtime\node_modules\.bin\social.cmd auth login` on Windows. The pinned runtime declares a newer Node engine than TrendRelay's Node 22 minimum, so installation records npm's engine warning; its version and read-only help surfaces were verified locally on Node 22. Configure its Meta profile, or set only the non-secret default account identifier `META_AD_ACCOUNT=act_...` in `.env`. A real briefing requires Meta API permissions such as `ads_read`; it may incur platform rate limits. No live briefing or account mutation is performed during installation.