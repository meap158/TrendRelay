# Third-party project catalog

Every external GitHub project incorporated by TrendRelay is recorded here. Managed capability providers are also pinned in `config/tool-catalog.json`, installed outside Git under `.tools/`, and activated separately. Supporting npm runtime packages are locked in `package-lock.json`. Installation does not grant permission to use a tool outside its license or platform terms.

| Project | Purpose | License posture | TrendRelay status |
| --- | --- | --- | --- |
| [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) | Douyin media acquisition | MIT; permitted with platform/rights constraints | Adapter ready |
| [gitroomhq/postiz-agent](https://github.com/gitroomhq/postiz-agent) | Social publishing | AGPL-3.0; distribution/network-use obligations apply | Adapter ready |
| [gitroomhq/postiz-app](https://github.com/gitroomhq/postiz-app) | Native self-hosted social publishing service | AGPL-3.0; distribution/network-use obligations apply | Embedded service ready on Windows |
| [mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill) | Recent multi-source trend research | MIT | Adapter ready; installed and active locally |
| [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) | Agentic video production | AGPL-3.0; distribution/network-use obligations apply | Guarded preflight and isolated local clip rendering ready |
| [eugeneware/ffmpeg-static](https://github.com/eugeneware/ffmpeg-static) | Packaged FFmpeg/ffprobe media runtime | GPL-3.0-or-later; source and notice obligations apply | Locked supporting runtime dependency |
| [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) | Research channel discovery and diagnostics | MIT; authenticated channels carry account risk | Sanitized local diagnostics ready |
| [TheMattBerman/meta-ads-kit](https://github.com/TheMattBerman/meta-ads-kit) | Read-only Meta Ads performance briefings | MIT; Meta API permissions and account terms apply | Adapter ready; installed and active locally |
| [promisingcoder/MetaAdsCollector](https://github.com/promisingcoder/MetaAdsCollector) | Public Meta Ad Library competitive research | MIT; reverse-engineered transport remains subject to platform and legal constraints | Embedded Research adapter ready |
| [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | Multi-platform social research | Non-commercial learning/research only | Catalogued; install and activation blocked |

The local `/tools` page and `npm run tools --` expose catalog status. Only pinned-source installation and local activation state are automated. Provider credentials and platform account authorization remain explicit follow-up steps. Postiz itself is local and uses an app-managed local admin session; each social platform still requires its own OAuth app and account consent. Meta Ads Kit is the exception for runtime packaging: TrendRelay installs its exact pinned Social Flow CLI dependency inside the tool directory, while still leaving authentication to the account holder.
