# NanmiCoder/MediaCrawler

- Repository: https://github.com/NanmiCoder/MediaCrawler
- Audited revision: `0625e01a6bc717a3fc9c96d3dac7fb8957043838`
- License: Non-Commercial Learning License 1.1
- TrendRelay status: catalogued, installation and activation blocked

MediaCrawler supports browser-assisted public-post, creator, and comment research across Xiaohongshu, Douyin, Kuaishou, Bilibili, Weibo, Tieba, and Zhihu. Its normal setup uses Python/uv, Node.js, and optionally Playwright or an existing Chrome debugging session.

MediaCrawler is a normal catalogued source-checkout provider: installable and activatable from `/tools`, and off until an operator turns it on.

Its upstream README asks against large-scale crawling, which shapes how TrendRelay uses it. Requests stay bounded and operator-initiated, the same posture as the other research providers: no background sweeps, no unattended schedules, and a limit on every collection.
