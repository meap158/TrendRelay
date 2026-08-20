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
 */
export const AUTHORITIES: readonly (readonly [string, string])[] = [
  ["assist", "Assist — approve every post"],
  ["auto_draft", "Auto draft — engine drafts, approve every post"],
  ["run_by_exception", "Run by exception — approve every post (recommended)"],
  ["autonomous", "Autonomous — post without approval (earned)"],
];
