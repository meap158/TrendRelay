# Media worker

Isolated runtime for downloading, FFmpeg/ffprobe processing, transcription, OCR, hashes, proxies, thumbnails, and quality-control checks.

## Available providers

- `jiji262/douyin-downloader` through `npm run douyin --`: pinned, isolated Douyin video, gallery, collection, music, and profile batch downloads with retries, SQLite deduplication, incremental mode, and optional browser fallback.

Provider source and credentials must remain outside the core. Downloaded media is reference material until rights classification explicitly permits publication.
