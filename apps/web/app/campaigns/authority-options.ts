/**
 * How much of the posting runs without a person, offered in one list.
 *
 * Its own module because two surfaces choose it - the header a campaign is run
 * from, and the form it is described in - and the labels are the part that
 * must not drift. A second copy of four strings is a second copy of a promise
 * about what each level does.
 *
 * Not in the panel, though the panel is where it is mostly used: the panel is
 * loaded on demand, and importing a constant out of it would pull the whole
 * thing into the page that only needed four words.
 *
 * The wording says approval where approval happens, which is every level but
 * the last. That reads repetitive and is the point: the levels below
 * autonomous differ in what they prepare, not in whether they ask.
 *
 * The third string is what the level actually does, for the tooltip on the
 * dial and the hover on each option. It says only what the code does: below
 * Autonomous every frozen post waits in the inbox, and the one level that
 * changes what approving means is Auto draft, which files a draft whatever
 * Delivery says. Writing a difference into Assist that the pipeline does not
 * make would be a promise nothing keeps.
 */
export const AUTHORITIES: readonly (readonly [string, string, string])[] = [
  [
    "assist",
    "Assist — approve every post",
    "Every post waits in the approval inbox. Approving it sends it the way "
    + "Delivery says.",
  ],
  [
    "auto_draft",
    "Auto draft — engine drafts, approve every post",
    "Every post waits in the approval inbox, and approving it files a draft in "
    + "the engine whatever Delivery says. Nothing is scheduled or published "
    + "from here, so you publish from the engine yourself.",
  ],
  [
    "run_by_exception",
    "Run by exception — approve every post (recommended)",
    "Every post waits in the approval inbox. The level to run a new campaign "
    + "at: Autonomous is earned from the posts confirmed under it.",
  ],
  [
    "autonomous",
    "Autonomous — post without approval (earned)",
    "A finished post goes out without waiting for you; only an unfinished one "
    + "comes back. Unlocked after ten provider-confirmed posts, and given up "
    + "by choosing another level here.",
  ],
];
