# Plugin specification

Plugins provide replaceable capabilities such as `trend.source`, `media.download`, `affiliate.catalog`, `ai.video`, and `social.publish`.

Every plugin declares its version, capabilities, allowed network domains, required secret references, input/output schemas, timeouts, and health check. Plugins run out of process, receive scoped credentials, and support idempotent operation IDs. Downloader plugins never receive publishing credentials.
