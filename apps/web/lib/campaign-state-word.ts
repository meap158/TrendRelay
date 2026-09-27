/**
 * The one word for where a campaign stands, autopilot included.
 *
 * A campaign has two independent switches: its status - where it sits in its
 * life, draft or active or archived - and whether its autopilot runs. The row
 * said only the first, so an active campaign whose autopilot a circuit breaker
 * had switched off read exactly like a working one, and one sat stopped for
 * days with "active" under its name.
 *
 * Folding the second switch into the word rather than adding a second word:
 * the row has one line for this, and "active · inactive" is not a sentence.
 * The status glyph beside the name still says the campaign itself is live, and
 * the stopped mark still says why - this only stops the word claiming that
 * something is happening when nothing is.
 *
 * Off is `false` and only `false`. Null is a campaign with no autopilot at
 * all, which is not the same as one that was switched off, and undefined is an
 * endpoint that does not report it - neither is evidence that posting stopped.
 */
export function campaignStateKey(
  status: "draft" | "active" | "archived",
  autopilotRunning: boolean | null | undefined,
): "draft" | "active" | "archived" | "inactive" {
  return status === "active" && autopilotRunning === false ? "inactive" : status;
}
