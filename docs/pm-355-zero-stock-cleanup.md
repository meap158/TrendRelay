# PM-355 inventory cleanup

On 8 November 2026, the owner requested removal of pending Mây Meo Sleepwear
posts featuring products with no stock. ShopPilot's saved catalogue at
`S:/Vibe Marketing/ShopPilot/runs/products/catalog.json` was read without
contacting Shopee. Its snapshot time was 21:17:44 Asia/Bangkok that day.

Products were matched by exact Shopee item ID from Attribution identifiers
and product URLs. MIDNIGHT SWEET, item `42979470226`, had total stock zero.
Every active campaign post's attached products had a catalogue match.
Offer attachments, primary media product attribution and visible MIDNIGHT
mentions were checked.

Five queue entries were retired and unpinned through the campaign MCP tools:

| Queue entry | Previous use |
| --- | --- |
| `queued_ce60a01a9a654cbc938b5676ea3fefdb` | Unposted styled flat lay, pinned 24 November at 15:00 Bangkok |
| `queued_06bf747fbc584a18bc9c3ccad243ebc1` | Previously used MIDNIGHT post |
| `queued_a6297f307a014e90882f2241ead13c4f` | Previously used bed flat lay |
| `queued_932795d865334e259e7eb080cbaddc36` | Previously used multi-product bed flat lay including MIDNIGHT |
| `queued_7664d229cf134696b86f80cd4c582e22` | MIDNIGHT Linh editorial, handed to the provider |

The last entry also had a provider-scheduled Facebook post for 9 November
at 21:00 Bangkok. Local execution `pubexec_6019ed4d187f404a8fc9050326dacfe6`
said `published`, but Zernio post `6af081588bc5badc464d9b4f` was actually
`scheduled`, with its platform entry `pending` and no publication time.
It was deleted through the configured Zernio connection. The provider returned
`Post deleted successfully`; a subsequent GET returned `Post not found`.
No live social post was deleted. The existing local execution receipt remains
as historical evidence of the earlier provider handoff.

Validation compared the full queue before and after: only the five target
states and their pins changed. No active queue entry still features the
zero-stock product. All 104 campaign execution records were checked; the
cancelled provider post was the only future delivery among the affected entries.
Media files, captions, product links, publication history, campaign authority
and unrelated queue entries were preserved. No services were restarted.

Local receipts are in `.data/mm-stock-cleanup/`: `inventory-snapshot.json`,
`audit.json`, `before-apply.json`, `after.json`, `validation.json`,
`remote-before.json`, `remote-cancel.json` and `remote-validation.json`.
This is a cleanup against the saved stock snapshot, not an automatic stock gate.
