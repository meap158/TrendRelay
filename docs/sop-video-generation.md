# SOP: making a product video with Gemini Web (Veo)

Follow it top to bottom. Each gate has a check that must pass before the next step; every failure below names its cause.
Background and limits: `docs/gemini-web-video.md`. Providers are listed in Tools > Video providers.

## Run it as a command

Everything below is automated by `scripts/veo_video.py`. The only steps left to a person are the ones no program can do:
signing in to Google and pasting a fresh Cookie header into Tools, and restarting the API and worker (the operator's).

| Command | Spends | What it does |
| --- | --- | --- |
| `python scripts/veo_video.py doctor` | no | Gates 1-3: cookie saved and switched on, account slot not the free account, bridge started if it is not answering and signed in, Check passes, the API offers Gemini Web |
| `python scripts/veo_video.py slots [--apply]` | no | Gate A: starts a throwaway bridge per account slot on a spare port, reads each model list, picks the slot with the Pro models, and with `--apply` saves it in Tools |
| `python scripts/veo_video.py generate --confirm [--draft ID]` | one video | Runs the doctor gates, queues the newest pending single-product video draft (or the one given) through the API as the Attribution button does, waits up to 15 minutes, then verifies Gate C |
| `python scripts/veo_video.py verify --asset ID [--recipe R]` | no | Gate C alone, for any filed clip |

Gate C, checked by the command: the asset is a video; it carries the tags `ai-video`, `veo`, `gemini-web` and the recipe; it is
720x1280 H.264 with audio and 5-15 s long; the Library file downloads at exactly the recorded size; and a Library search for
`gemini-web` finds it. It also saves one frame to `.data/gemini-web/verify-<asset>.jpg`; compare that with the product picture,
the one check left to eyes. Exit codes: 0 done, 1 a gate failed before anything was spent, 2 refused (no `--confirm`), 3 the
generation failed, 4 the clip was made but a check on it failed. It never resends a request and never falls back to another
provider. A failure prints what to do; read the failure table before running `generate` again.

**The cookie goes stale.** Google rotates the session cookie, so the copy saved in Tools stops signing in within hours. A bridge
that is already running keeps its own session alive and keeps working, but a restart, or `slots`, then fails with "invalid or
expired". `doctor` and `slots` say so. Fix: paste a fresh Cookie header in Tools, save, run `doctor --restart-bridge`.

The sections below are the same flow written out, for when a gate fails and you need the reason.

## 0. What goes into a clip, and what comes out

| Provider | Product picture sent | Character pictures sent | Product described in words | Output |
| --- | --- | --- | --- | --- |
| Grok (xAI) | yes, as a reference | yes | no | 10 s, 9:16, bucket-delivered |
| Gemini (official API) | yes, as the first frame input | no | no | 8 s, 9:16 |
| **Gemini Web** | **yes**, attached as `input_reference` (patched bridge) | yes, portrait and sheet | no | about 10 s, 720x1280, audio |

Gemini Web takes at most three pictures per request: the product picture first, then the character's. The stored prompt is
sent unedited; its "attached product image" wording is true because the picture is attached. A provider that cannot take
pictures (none today) gets a context block ahead of the prompt instead: the lead product's name, up to three others it is
shown with, its listing description (700 characters) and the character's name (`prompt_context`, used when a provider
has `takes_image` off). The draft needs a Library image selected before it can be queued.

## 1. One-time setup

1. Sign in at gemini.google.com with an account whose plan includes Veo (Google AI Pro or Ultra). A spare account is
   wise: this uses a web session, which Google's terms do not allow.
2. DevTools (F12) > Network > reload > pick a request whose address contains `batchexecute` (`rpcids=otAQ7b`) > copy the
   whole value of the `Cookie` request header. It must contain both `__Secure-1PSID` and `__Secure-1PSIDTS`.
3. Tools > Video providers > Gemini Web: paste it in the cookie box, switch **Offer Gemini Web** on, save.
   Saving also writes `.data/gemini-web/.env`, the file the bridge reads. Never paste the cookie anywhere else.
4. Find the right **account slot** (next section) and save it in the same card. Do not skip this.

## 2. Gate A: the account slot

The cookie holds every Google account signed in to that browser. Without a slot the bridge uses the first one, which can
be a free account: Check still passes and a job is still created, but Gemini answers "If you upgrade your subscription I
can create that video for you today." and no video ever arrives (the job sits at 95% until the bridge times out).

Find the slot without spending anything. With the bridge stopped, for each N in 0, 1, 2, 3, start it with
`GEMINI_AUTH_USER=N` and read `http://127.0.0.1:4981/openai/v1/models`:

- a free account lists only `gemini-auto`;
- a Pro account lists the Pro models (for example `gemini-3.1-pro`);
- an error such as status 1033 means that slot is not usable.

Set the slot that lists the Pro models in Tools (**Gemini Web account slot**). On 2026-10-26 it was `1`.

## 3. Gate B: the bridge

1. `python scripts/gemini_web_bridge.py` (clones the pinned release, applies `scripts/gemini_web_bridge.patch` so a video
   can carry pictures, and builds; Go needed; everything stays under `.data`). It rebuilds when the patch was not yet
   applied. It prints the address. It passes `PORT` explicitly because this machine's shell sets `PORT`, which the bridge would otherwise
   obey over its `.env`.
2. Restart the bridge after every cookie or slot change. It reads `.env` only at start.
3. Tools > Gemini Web > **Check**. The only passing answer is "The Gemini Web bridge is signed in (N models)". Anything else:
   - "not answering" - the bridge is not running or the address is wrong;
   - "running but is not signed in" - the cookie is invalid or expired; copy a fresh one and restart.
4. Restart the API worker once after first setup, so it claims jobs for the provider.

## 4. Generate

1. Attribution > Pending drafts > a **video** draft with one product > Review. (A shot of several products takes its file
   from outside; the draft must have a Library image chosen.)
2. Choose **Gemini Web** and confirm. One request goes out; each clip uses one unit of that account's daily video quota.
   The bridge makes one video at a time: a second request while one runs is refused, not queued.
3. Wait 1-3 minutes. Do not queue it again. A job that ends "transient" resumes the same request; it is never resent.

## 5. Gate C: check the result

All of these must hold. The first is the app, the rest are the file and the Library.

1. The draft shows its file and leaves the pending list.
2. Find the clip: Library search `ai-video`, `veo`, `gemini-web` or the recipe id. Every clip made by a provider is filed with
   the hashtags `ai-video`, its provider (`grok`; `veo` + `gemini`; `veo` + `gemini-web`) and the draft's recipe id, and is
   linked to the draft's product, recipe and character like every other creative (so "Open in Library", the recipe filter and
   the batch viewer all find it).
3. Probe the file: `ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,duration -of compact <file>`
   should show an H.264 video stream of 720x1280, an AAC audio stream, and a duration near 10 s.
4. Look at a frame (`ffmpeg -ss 4 -i <file> -frames:v 1 frame.jpg`) beside the product picture. It must show that product
   in the scene the prompt describes. A different garment means the picture did not reach Gemini: check that the bridge
   log line "Video generation submitted" came from a patched build, and that the draft's Library image is the right one.

## 6. When it goes wrong

| What you see | Cause | Fix |
| --- | --- | --- |
| Job at 95% for minutes, then "did not finish in time" | free account slot, or the plan lost Veo | Gate A; read the bridge log for the upgrade sentence |
| Job ends "not ready before the deadline; it may still appear in Gemini Web conversation c_..." | Gemini accepted the prompt ("I'm generating your video") but delivered nothing in 10 minutes: capacity or a silent daily limit. Seen 2026-10-26 at the third clip of the day; the first two arrived in about 80 s | Open that conversation at gemini.google.com (signed in to the same account) to see whether the clip appeared late. Wait before trying again; the worker does not resend it, so one attempt costs at most one unit |
| "limit reached" / `quota` | the account's daily video quota is spent | wait for the reset or use another account's cookie |
| "already making a video" | another clip is running on the bridge | wait; do not retry in a loop |
| `moderation` | Gemini refused the prompt | change the draft's recipe or wording; it is not retried |
| "bridge is the unpatched one" | the bridge was built without the picture patch | run `scripts/gemini_web_bridge.py` again; nothing was spent |
| Connection dropped while sending | pictures larger than the bridge's body limit (unpatched default is 4 MB) | the patch raises it to 40 MB; rebuild |
| `slots` or a bridge restart says the cookie is "invalid or expired" while a running bridge still answers | the saved cookie went stale; the running bridge holds a rotated session | fresh cookie in Tools, then `doctor --restart-bridge`. Do not restart a working bridge needlessly |
| Check says "not signed in" | cookie expired or signed out | fresh cookie, restart the bridge |
| Bridge exits at start | cookie missing `__Secure-1PSID` or `__Secure-1PSIDTS` | copy the whole Cookie header |
| Bridge on port 5180, Check cannot reach it | the shell's `PORT` overrode `.env` | start it with the script, not by hand |
| Disk errors while building the bridge | `C:` is full; Go cached there | the script sets every Go cache under `.data` |

## 7. Rules that keep it correct

- One clip, one request. Never script a retry around a refused or timed-out job; check the bridge log first.
- The provider never falls back to another by itself, because that would spend again.
- The cookie lives in `.env` and `.data/gemini-web/.env` only; Tools shows it masked; it is never logged or committed.
- A change to the cookie, slot or bridge address means: save in Tools, restart the bridge, run Check, then generate.
