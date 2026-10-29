# PM-355 round review

Checked against the running TrendRelay API on 2026-10-29. Inventory came from
`S:/Vibe Marketing/ShopPilot/runs/products/catalog.json`, read without calling
Shopee. The campaign is Mây Meo Sleepwear.

The existing round contains 48 drafts: 33 carousels, 11 videos and four single
images. Its 167 actual media files are all distinct. The earlier board note's
215 references counted the 48 primary asset references again as files.
All 54 source creative drafts were checked against their Library assets and
the queue. Their complete image sets remain together, in order.

All 48 drafts now have reserved posting slots from 16 November at 09:00 through
25 November at 15:00, Asia/Bangkok. These follow the existing queue's final
reserved slot on 15 November. The campaign remains autonomous; every post in
this round remains a draft that the scheduler cannot publish. Existing posts'
approval states, media, captions and slot reservations were preserved.

The schedule starts with SUGAR DOLL (48), DOLLY DOT (34), DOTTIE (21),
SUMMER ONE (13), then stock-10 products, and finally CLOUDY (7). For a post
with several products, its rank uses the highest stock of its attached
campaign-tagged products. The four leading posts are product-only media;
the other 44 posts use the selected Hà My media. No new images were generated.

Fourteen captions were revised in Vietnamese to remove obsolete weekdays,
month openings, Halloween and 11.11 references. All 48 captions are distinct.
Equal-stock posts were spread into three carousels between videos, with
recipe and product changes where available.

## Acceptance still requiring a decision

Strict alternation is not met. In scheduled order, there are 25 adjacent pairs
with the same format, two with the same recipe and 36 with the same product
count. The four leading product-only posts are all single images, and three
use the same source recipe. Strict stock order keeps them together. The
stock-10 group has 33 carousels and ten videos, so its best possible maximum
carousel run is three. It now achieves that bound.

The owner must choose whether this stock-first sequence is acceptable or the
round should be expanded or regrouped for stricter alternation. The task is
ready for review of that tradeoff, and is not marked complete. Draft approval
remains an operator action in the campaign queue.

## Recheck

Run `.\.venv\Scripts\python.exe scripts/audit_maymeo_round.py` from the project
root while the operator's API is running. It reads the API and saved ShopPilot
catalogue, and writes `.data/mm-round/acceptance-audit.json`. It exits nonzero
while any strict acceptance check fails. Build receipts, before/after queue
snapshots, the stock-ranked plan and per-slot receipts stay local in
`.data/mm-round/`. The audit makes no API writes and does not approve posts.
