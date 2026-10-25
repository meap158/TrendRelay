# Hook text overlays (PM-295)

A hook overlay is the plain on-screen text a short video opens with to catch a viewer, in the style UGC
clips use. It is not a caption: it is not spoken, not word timed, and it is a few lines for a few
seconds. The code is `services/api/src/trendrelay_api/hook_overlay.py` (draft model, rules, batch,
render through the existing caption burner) and `hook_overlay_ai.py` (model-written options).

## Rules the code enforces, and why

| Rule | Value | Basis |
| --- | --- | --- |
| First line starts | within 1 s | The first 1 to 3 seconds decide whether a viewer stays. |
| Hook is over by | 5 s | It must not compete with the content it introduces. |
| Words per line | 7 or fewer (42 characters) | Creator guides suggest 5 to 8 words so it reads at a glance. |
| Lines on screen | at most 2, never overlapping | One idea at a time. |
| Time on screen | 900 ms plus 280 ms per word | Enough to be read once without pausing. |
| Look | bold, high contrast (outline or box) | Must read on any footage and with the sound off. |
| Position | top, 300 px clear (template asks 240); sides 130 px (asks 120); mid-frame uses 300 px sides to clear the button column | TikTok in-feed safe area, below. |

## Sources and what they do not settle

- TikTok in-feed ad safe zone on a 1080 x 1920 frame: 240 px top, 660 px bottom, 120 px each side, as
  reported by [Socialync](https://www.socialync.io/tools/safe-zones/tiktok) and
  [Upload-Post](https://www.upload-post.com/tools/safe-zone-checker/). Both say they read it from
  TikTok's own template; neither is TikTok's page. The button column on the right is an estimate of
  180 to 300 px wide from about 840 px down, so the sources disagree. More conservative figures exist
  ([Revid](https://www.revid.ai/tiktok-safe-zone): about 220 px top, 480 px bottom, 140 px right).
  Check TikTok Ads Manager's In-Feed specification before treating these as exact.
- The hook window and short text length come from creator-facing guides
  ([SocialKit](https://socialk.it/en/blog/short-form-video-strategy),
  [Skeepers](https://community.skeepers.io/blog/writing-text-hooks/)). They are practice, not platform
  documentation. Figures such as a 70% three-second retention floor or a 35 to 40% hook rate are
  single-vendor benchmarks and are not used as hard limits here.
- Testing: post two hook variants and compare three-second retention after a few days. That is a
  sensible method from the same guides, not a platform feature.

Each preset can change without code changes elsewhere; the limits are constants at the top of
`hook_overlay.py`, and the tests in `tests/test_hook_overlay.py` pin them.

## Status

Built: draft model and checks, timing, approval gate, batch, the AI proposal module (model injected).
To build: provider wiring, the stored draft and render job with API routes, the Library Hook tab and
batch dialog, translations, and a render test on a real clip.
