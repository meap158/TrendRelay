# OmniRoute and TrendRelay video generation (PM-280)

Evaluated 2026-10-26 from the public repository `cheaper-inference/OmniRoute` (MIT licence, TypeScript, very active) at its
current main: its README, `open-sse/config/videoRegistry.ts`, `open-sse/handlers/videoGeneration/googleFlow.ts` and the file
list for Veo. Nothing was run, no key was used and no clip was generated.

## What it is

An AI gateway: one OpenAI-style endpoint in front of hundreds of providers, with quota-aware fallback between them, token
compression, a dashboard, and many "free tier" and subscription-login providers. Video is one modality among them
(`/v1/videos/generations`, a provider table of base URL, auth type and wire format).

## Veo: what its own code says about a "web" (cookie or token) route

OmniRoute tried exactly that, as a provider called Google Flow: Veo through Google's internal `aisandbox-pa` endpoint with an
account OAuth bearer. Its registry entry now carries the result of live validation and the provider is de-listed:

- the documented-looking submit and poll paths return 404 on that host;
- the one path that does exist is undocumented and rejects the stored OAuth token with 401 (the scopes do not cover it);
- mutation endpoints only work from a headed browser session that passes reCAPTCHA.

So a server-side cookie or token route for Veo web **does not work** even for the project built around it. It would also mean
holding a consumer account's session as a credential, against the provider's terms, expiring and capable of getting the account
banned. TrendRelay does not need it.

## What works, and what TrendRelay already has

OmniRoute's working Veo routes are the official ones: Vertex AI (`veo-3.1-generate-001`, fast and 3.0/2.0 variants, bearer
key) and fal.ai (`veo3.1/lite`). TrendRelay already ships the official Gemini API route
(`integrations/video_generation.py`, model `veo-3.1-generate-preview`, image-conditioned, 9:16, resumable) next to xAI. Its
design is in several respects stricter than a gateway's:

| Concern | OmniRoute | TrendRelay |
| --- | --- | --- |
| Provider model | table entry plus wire-format handler | the same: one registry entry plus an adapter |
| Fallback on failure | automatic, to another provider | none, on purpose: a refusal that fell through would spend again |
| Retries | per request | resumes a request already paid for, never sends a second |
| Check before use | health probes | Check calls a models endpoint and never starts a video |
| Credentials | keys and OAuth pools in its own store | keys in the app's env store, shown masked |

Automatic fallback is the one idea that does not fit: for paid video it would silently double the spend. Everything else it
does, TrendRelay does already for the two providers it supports.

## Decision

- **Not adopted:** the gateway itself (it would sit between TrendRelay and every provider and hold more credentials), the Veo
  web/Flow route, and automatic fallback.
- **Built (small, safe, opt-in):** a model choice for Gemini, so Veo 3.1 Fast can be tried for cost without a code change.
  `VIDEO_PROVIDER_GEMINI_MODEL` accepts only the listed models and falls back to the default for anything else, so an edited or
  stale value never reaches a paid endpoint. Default unchanged. The fast model name is the Gemini API's documented one but was
  **not run here**; the first clip is the real test, and Check does not exercise it.
- **Possible later, needs the owner:** a Vertex AI or fal.ai entry (each needs the owner's account, key and per-clip budget),
  and per-provider spend tracking (count clips and cost per provider on the Tools card).

## Open questions for the owner

1. Is Veo 3.1 Fast acceptable quality for product clips? Try a few drafts with it before relying on it.
2. Is a second Veo route (Vertex or fal.ai) worth the extra account for price or availability reasons?
3. Is a visible per-provider spend counter wanted?
