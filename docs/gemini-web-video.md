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

1. Run the bridge on this machine (Docker image or `go run`), started from `.data/gemini-web/`, where Tools writes the `.env` the bridge loads (`PORT`, `GEMINI_COOKIES`,
   `GEMINI_AUTH_USER`). The bridge reads `.env` from its working directory only, and exits unless the cookie holds both
   `__Secure-1PSID` and `__Secure-1PSIDTS`. Restart it after saving a new cookie. The API does not start it.
2. In Tools, Video providers, Gemini Web: paste the cookie (the full `Cookie` request header of a `batchexecute` request
   in a signed-in gemini.google.com tab, with `GEMINI_AUTH_USER` set if that address has `/u/N/`), turn the switch on, and
   run Check. Check asks the bridge for its model list and starts no video.
3. The API worker needs a restart before it claims jobs for the new provider.

## Limits to know

- **Text-to-video only.** The bridge takes no image, so the product picture is not sent; the stored prompt carries the
  garment. A character's pictures are not sent either.
- **Fixed output.** About 10 seconds with audio, 9:16 (720x1280). Duration is Gemini's choice.
- **Quota.** Each clip uses one unit of the account's daily Gemini video quota. The bridge runs one video at a time and
  answers 429 when busy; the job reports that and does not queue a second clip.
- **Terms and risk.** This is a consumer session used as a credential. It is against Google's terms, can expire, and can
  get the account limited. Use a spare account. The cookie is stored in the local `.env` and in the bridge env file, shown
  masked in Tools, and never logged.
- **Unverified here.** Nothing has been run against a real account. Check proves only that the bridge answers; whether the
  cookie still works shows on the first clip. The bridge's own env-file loading (quoting of a cookie containing `;`) was
  not read from its source.
