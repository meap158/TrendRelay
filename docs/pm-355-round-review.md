# PM-355 round review

Validated against the running TrendRelay API on 2026-10-29 at 03:58 UTC.
Inventory came from `S:/Vibe Marketing/ShopPilot/runs/products/catalog.json`,
read without calling Shopee. The campaign is Mây Meo Sleepwear.

The final round contains 39 drafts: 19 carousels, 18 videos and two single
images. All 174 actual media files are distinct. Every one of the original
round's 167 files is preserved, with seven unused Hà My videos added.
All 61 source creative drafts were checked against their Library originals
and the live queue. Each source carousel remains complete and contiguous,
with its original card order. Every combined carousel fits Facebook's
ten-image limit.

The owner delegated the feed mix on 2026-10-29. Twenty-eight single-product
carousels became fourteen two-product carousels. Two four-product collections
became one eight-image collection. DOLLY DOT and DOTTIE became a two-image
comparison. Together with the seven new videos, these are 23 new drafts;
16 original drafts remain. The 32 superseded drafts are retired and unpinned,
preserving their history. They are excluded from the active round.

All 39 active drafts have reserved slots from 16 November at 09:00 through
23 November at 18:00, Asia/Bangkok. These follow the existing queue's final
reserved slot on 15 November. The campaign remains autonomous; every active
post in this round remains an unapproved draft. Existing posts outside the
round retain their approval states, media, captions and reservations.

The schedule starts with SUGAR DOLL (48), a DOLLY DOT (34) and DOTTIE (21)
comparison, SUMMER ONE (13), then stock-10 products, and finally CLOUDY (7).
For a multi-product post, rank uses the highest stock of its attached
campaign-tagged products. The three leading posts are product-only media;
the other 36 posts use selected Hà My media. No new media was generated.

All 39 Vietnamese captions are distinct. The 23 new captions vary between
stories, jokes, observations, practical viewing guidance and specific
questions. Retained captions were already revised to remove obsolete date
references. The added clips were visually sampled at one, four and eight
seconds before being queued.

## Passing acceptance

The live audit reports zero stock inversions and zero adjacent equal formats,
editorial recipes or product counts. The opening runs image, comparison
carousel, image. Thereafter, multi-product carousels alternate with
single-product videos.

Recipe means the finished post's editorial arrangement. For example, the
DOLLY DOT and DOTTIE post is a two-product sunlight comparison assembled from
two individual source images. Its source generation recipe remains
`sunlit_floor_shadow_heap` for both images. Merged posts retain all original
source draft IDs and source recipe names in the local manifest. This distinction
does not change or split the source media.

The delegated decision task was removed from the owner desk and its pages
rebuilt. No further editorial decision is required to finish PM-355. Promoting
drafts remains the campaign's normal operator approval step.

## Recheck

Run `.\.venv\Scripts\python.exe scripts/audit_maymeo_round.py` from the project
root while the operator's API is running. It reads the API and saved ShopPilot
catalogue, and writes `.data/mm-round/acceptance-audit.json`. It exits nonzero
if any acceptance check fails. Active build receipts and the selection manifest
are `built.json` and `selection.json` in that directory. Original manifests
have the `original-` prefix. Regrouping, creation and slot receipts, before/after
queue snapshots and the sampled video contact sheet remain there as local
evidence. The audit makes no API writes and does not approve posts.
