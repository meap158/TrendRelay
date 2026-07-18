# Douyin batch downloader plugin

This local-process plugin adapts the pinned `jiji262/douyin-downloader` provider to TrendRelay's media capability boundary. Use the repository-root `douyin.cmd` entry point; do not import upstream internals into core modules.

The default profile limit is 50 items per selected mode. Use `--limit 0` only when an intentional full crawl is appropriate. SQLite and local-file deduplication are enabled, so repeated and incremental runs do not duplicate completed media.
