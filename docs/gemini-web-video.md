# Gemini Web video (Veo through a signed-in session)

An opt-in video provider next to xAI and the official Gemini API. It makes a clip with the Gemini web app's Videos tool
using a Google account's browser session, through a bridge that runs on this machine. It is off until its switch is on
and Check has passed.

## What it is built on

[ntthanh2603/gemini-web-to-api](https://github.com/ntthanh2603/gemini-web-to-api) (MIT, Go). It turns the Gemini web app
into a local OpenAI-style server; its video jobs are `POST /openai/v1/videos`, `GET /openai/v1/videos/{id}` and
`GET /openai/v1/videos/{id}/content`. The adapter in `integrations/video_generation.py` (`generate_gemini_web`) speaks
only that. [Sophomoresty/gemini-web2api](https://github.com/Sophomoresty/gemini-web2api) was read as well; its README
documents chat, tools and images but no video, so nothing is built on it.

## Setting it up

1. Run `python scripts/gemini_web_bridge.py`. It clones and builds the bridge once (Go is needed; every cache stays under
   `.data`) and runs it from `.data/gemini-web/`, where Tools writes the `.env` the bridge loads (`PORT`, `GEMINI_COOKIES`,
   `GEMINI_AUTH_USER`). The bridge reads `.env` from its working directory only, and exits unless the cookie holds both
   `__Secure-1PSID` and `__Secure-1PSIDTS`. Restart it after saving a new cookie. The API does not start it. The script passes `PORT` explicitly because the bridge
   never lets `.env` override a variable the shell already sets, and this machine's shell sets `PORT`.
2. In Tools, Video providers, Gemini Web: paste the cookie (the full `Cookie` request header of a `batchexecute` request
   in a signed-in gemini.google.com tab, with `GEMINI_AUTH_USER` set if that address has `/u/N/`), turn the switch on, and
   run Check. Check asks the bridge for its model list and starts no video.
3. The API worker needs a restart before it claims jobs for the new provider.

## Limits to know

- **The account slot must be the one with video.** The cookie header holds every account signed in to that browser, and
  without `GEMINI_AUTH_USER` the bridge uses the first, which can be a free account. On a free account Check passes
  ("signed in") and the job is created, but Gemini answers the submit with "If you upgrade your subscription I can create
  that video for you today." and no video follows; the bridge keeps the job `in_progress` (progress creeping to 95) until
  its own timeout and shows the reply only in its log. Compare slots without spending anything: start the bridge with
  `GEMINI_AUTH_USER` set to 0, 1, 2 and read `/openai/v1/models`. A free account lists only `gemini-auto`; a Pro account
  lists the Pro models. Set the winning number in Tools (Gemini Web account slot).
- **Verified live (2026-10-26).** Slot 1 (Pro): a pending draft's stored prompt produced a 10-second 720x1280 H.264/AAC clip in
  about 70 seconds through `generate_gemini_web`, and it matched the prompt.
- **Text-to-video only.** The bridge takes no image, so the product picture is not sent; the stored prompt carries the
  garment. A character's pictures are not sent either.
- **Fixed output.** About 10 seconds with audio, 9:16 (720x1280). Duration is Gemini's choice.
- **Quota.** Each clip uses one unit of the account's daily Gemini video quota. The bridge runs one video at a time and
  answers 429 when busy; the job reports that and does not queue a second clip.
- **Terms and risk.** This is a consumer session used as a credential. It is against Google's terms, can expire, and can
  get the account limited. Use a spare account. The cookie is stored in the local `.env` and in the bridge env file, shown
  masked in Tools, and never logged.
- **Still unverified.** The cookie's lifetime (the bridge rotates `__Secure-1PSIDTS` itself, but a Google sign-out or
  security check ends the session), and the worker path: the clip above went through the adapter directly, not through a
  queued draft, so filing it onto a draft from the Attribution screen is the next thing to try.
