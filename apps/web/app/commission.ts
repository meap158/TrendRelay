/**
 * What an affiliate offer pays, said the same way everywhere it is said.
 *
 * There were two spellings of this and they disagreed. Attribution wrote a rate
 * as `8%` or `8.25%` and a flat fee as money; the campaign composer wrote
 * `8% commission` from the same basis points and had no idea what a flat fee
 * was. A product's rate is the reason one offer was chosen over another, so it
 * belongs beside the product wherever the product appears - and reading `8%` in
 * one place and `8.25%` in another for the same offer is how somebody stops
 * trusting either.
 *
 * Basis points, because that is how the networks quote and store it: 825 is
 * 8.25%. The decimals are shown only when they carry something, so a flat 8%
 * does not read as `8.00%` and imply a precision nobody stated.
 */
import { money } from "./attribution/format";

export type CommissionBearing = {
  commission_bps?: number | null;
  commission_flat_cents?: number | null;
  currency?: string | null;
};

/** Just the rate, or an empty string when the offer does not state one. */
export function commissionRate(offer: CommissionBearing | null | undefined): string {
  const bps = offer?.commission_bps;
  if (bps === null || bps === undefined) return "";
  return `${(bps / 100).toFixed(bps % 100 ? 2 : 0)}%`;
}

/**
 * The whole of what this offer pays: a percentage, a fixed amount, or both.
 *
 * Both is not hypothetical - a network can pay a rate and a per-order bonus,
 * and showing only the rate understates it. Empty when the offer states
 * neither, so a caller can leave the space alone rather than print a dash.
 */
export function commissionLabel(offer: CommissionBearing | null | undefined): string {
  const parts: string[] = [];
  const rate = commissionRate(offer);
  if (rate) parts.push(rate);
  const flat = offer?.commission_flat_cents;
  if (flat !== null && flat !== undefined && flat > 0) {
    parts.push(money(flat, offer?.currency || "USD"));
  }
  return parts.join(" + ");
}
