# Third-party project catalog

Every external GitHub project incorporated by TrendRelay is recorded here. Managed capability providers are also pinned in `config/tool-catalog.json`, installed outside Git under `.tools/`, and activated separately. Supporting npm runtime packages are locked in `package-lock.json`. Installation does not grant permission to use a tool outside its license or platform terms.

| Project | Purpose | License posture | TrendRelay status |
| --- | --- | --- | --- |
| [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) | Douyin media acquisition | MIT; permitted with platform/rights constraints | Adapter ready |
| [gitroomhq/postiz-agent](https://github.com/gitroomhq/postiz-agent) | Social publishing | AGPL-3.0; distribution/network-use obligations apply | Adapter ready |
| [mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill) | Recent multi-source trend research | MIT | Adapter ready; installed and active locally |
| [calesthio/OpenMontage](https://github.com/calesthio/OpenMontage) | Agentic video production | AGPL-3.0; distribution/network-use obligations apply | Guarded preflight and isolated local clip rendering ready |
| [eugeneware/ffmpeg-static](https://github.com/eugeneware/ffmpeg-static) | Packaged FFmpeg/ffprobe media runtime | GPL-3.0-or-later; source and notice obligations apply | Locked supporting runtime dependency |
| [Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) | Research channel discovery and diagnostics | MIT; authenticated channels carry account risk | Sanitized local diagnostics ready |
| [NanmiCoder/MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | Multi-platform social research | Non-commercial learning/research only | Catalogued; install and activation blocked |

The local `/tools` page and `npm run tools --` expose catalog status. Only pinned-source installation and local activation state are automated. Provider-specific credentials, browsers, system packages, and upstream setup scripts remain explicit follow-up steps.
