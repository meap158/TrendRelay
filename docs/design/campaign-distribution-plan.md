# Campaign distribution: media mix, page eligibility and burst days

Status: plan, not built. Written from a run on the Mây Meo Shop campaign where
every one of these was done by hand, post by post, over MCP: a daily
image/video pattern, videos kept off one page, and a one-off heavier day. Each
section below turns one of those manual passes into a rule the scheduler keeps.

## What exists today

- **Pins** (`campaign_slots.pin_item_to_slot`, MCP `pin_post_slot`): one post,
  one moment, optionally one account (`destination_id`). A pin is one outing;
  it clears when that outing publishes.
- **Per-post page skip** (`CampaignQueueItem.skipped_destination_ids`,
  migration `20261010_0092`, MCP `set_post_skipped_destinations`). One
  predicate, `CampaignQueueItem.skips_destination`, is read by the scheduler's
  eligibility, the slot picker, `day_slots` (status `skipped` for the post being
  placed), Publish now (even forced), and campaign duplication (ids remapped).
  The edit refuses unknown accounts and skipping every account, and it releases
  a pin on an account that is newly skipped.
- **Per-post format** (`post_type_overrides`) and the per-account daily cap.

What is missing is anything that works **across** posts. Every pattern above
had to be built by pinning 38 posts one at a time, and it stops the moment the
pins are spent.

## 1. Media mix per account

A campaign or account says what kind of post each slot wants, and the
scheduler fills it from the queue.

- **Shape.** `distribution_rules` on the autopilot, with optional per-account
  overrides beside `posting_preset_id`, resolved in the same order as posting
  times (account → campaign → default). Two forms, deliberately few:
  - `pattern`: an ordered list over the day's slots, e.g.
    `["image", "video", "image", "video", "image"]`. Slot *n* wants entry *n*;
    more slots than entries wrap around.
  - `quota`: a per-day count per kind, e.g. `{"image": 3, "video": 2}`, with
    the order left to the rotation.
- **Kinds.** Derived, never stored twice: `video`, `image` (carousel),
  `text`. One predicate (`media_kind_of(item)`) shared by the scheduler,
  previews and MCP, like `media_is_complete`.
- **When a kind runs out.** An explicit `fallback` per rule, since nobody
  should have to guess: `fill_any` (take the next eligible post of any kind),
  `leave_empty`, or `hold` (skip the slot and say so in the run note). The
  default is `fill_any`, so adding a rule never silences an account.
- **Pins win.** A pinned post takes its slot whatever its kind, and counts
  toward that day's quota, so a pinned video at 12:00 satisfies the pattern
  rather than displacing it.
- **Forecast.** The autopilot read model gains a per-kind runway: "videos last
  until Oct 18, images run out Oct 17". This run hit that wall (24 image slots,
  22 images) and only found it by arithmetic.

## 2. Page eligibility beyond one post

Today's skip is per post. Two broader rules cover what it was standing in for:

- **Account accepts kinds.** `CampaignDestination.accepted_media_kinds`
  (empty = all). "Homewear takes images only" becomes one setting rather than
  sixteen skips, and new videos inherit it. It goes through the same eligibility
  path as `skips_destination` - a second predicate,
  `destination_accepts(item, destination)`, called beside it, never inlined.
- **Already on this page.** The rest window already matches on Library asset
  per account (`latest_asset_elsewhere`), but only for posts TrendRelay made.
  The page here already had the clips from before the campaign. Add an
  "already posted here" ledger per (account, asset) that can be filled by hand
  ("mark as already on this page") or from an engine's post history where
  one exposes it. The scheduler then treats it exactly like its own history,
  and no per-post skip is needed for the common case.

## 3. Burst days and one-off overrides

The Oct 10 request - push more posts than usual on one date, pinned - had
nowhere to go: posting times recur by weekday, and the daily cap of 5 is per
account every day.

- **Dated slots.** `PublishingSlot` gains an optional `on_date`, so extra times
  land on that one day only. A weekday slot recurs every week, which is the
  wrong answer for "just Saturday the 10th".
- **Dated cap.** `daily_cap_overrides: {date: n}` on the autopilot (and per
  account), read by the same cap check that counts other campaigns' posts.
- **One action, previewed.** A "burst day" operation in the app and over MCP
  that adds the dated slots, raises that day's cap, and optionally pins a
  chosen set of posts across them in one go. It shows the result before
  committing, and leaves no recurring change behind.

## 4. Spacing

- The same product, or the same asset, not twice within *N* hours on one
  account, and a separate setting for the same asset across the campaign's
  accounts. This run had three CHERRY clips (two of them the identical file)
  and had to space them by hand.
- Implemented as a ranking penalty in the existing candidate loop, not a hard
  filter, so a thin queue still posts. A hard minimum is opt-in.

## 5. Interfaces

- **Post editor:** a "Pages" row with one chip per account; a skipped chip is
  struck through. The tooltip says the post won't be sent there.
- **Account settings:** "Media this page takes" (all / images / videos /
  text), and "Mark Library items as already on this page".
- **Distribution panel** on the campaign: pattern or quota per account, the
  fallback, and the forecast. A seven-day strip shows each slot's wanted kind,
  the post planned for it, and any gaps.
- **MCP:** `get_distribution_rules` (read), `set_distribution_rules` and
  `create_burst_day` as schedule-class writes (same policy argument as posting
  times: they decide when and where approved work goes, never whether);
  `skip_destination_ids` on `create_campaign_post`; a bulk form of
  `set_post_skipped_destinations` that names every item it changed; and a
  reviewed SOP `campaigns.distribute-posts` for the whole flow.
- Product copy stays plain, with no dates or campaign history in strings.

## 6. Robustness

- **One predicate per rule** (`skips_destination`, `destination_accepts`,
  `media_kind_of`, `media_is_complete`), shared by planner, pinning, Publish
  now, previews and MCP, so they cannot disagree.
- **Invariant tests**: a property-style test plans a randomised queue against
  randomised rules for a week and asserts that no planned outing breaks a skip,
  an accepted-kinds setting, a cap or a pin. One test per rule is too easy to
  satisfy by accident.
- **Explainable gaps**: every slot left empty because of a rule says which
  rule, in the run note and in the strip ("Homewear 12:00 - wants video; videos
  skip Homewear").
- **Unpostable warnings**: a post that no account can take under the current
  rules (all accounts skipped by settings, or every accepting account
  disabled) is flagged in the queue, rather than sitting approved and
  silently never going out.
- **Writes are validated where they land**: unknown account ids are refused,
  and contradictions (a pattern on an account that accepts no kind it names)
  are refused with the reason.
- **Lifecycle**: removing an account prunes its id from skips, overrides,
  rules and the ledger; duplication remaps all of them (skips and overrides
  already are). Duplication should also reset pins - today a copy inherits the
  original's `pinned_slot`, which is a separate fix worth making first.
- **Audit**: skip, rule, ledger and burst changes write audit entries with
  before and after, like other campaign settings.
- **Migrations additive with neutral defaults**, so an un-migrated rule reads
  as "no rule" and behaviour only changes when somebody sets one.
- **Worker**: scheduler changes need a worker restart (it does not hot-reload);
  every phase's hand-off note should say so.

## 7. Phasing

1. Duplication resets pins; `skip_destination_ids` on create; Pages chips in
   the post editor (the API already returns `skipped_destination_ids`).
2. `accepted_media_kinds` per account plus the "already on this page" ledger.
3. Media mix (`pattern` / `quota`, fallback, forecast) with the invariant test.
4. Burst days (dated slots, dated cap, one previewed action).
5. Spacing penalties.
6. MCP tools and the `campaigns.distribute-posts` SOP for each phase as it
   lands, not at the end.
