/**
 * How many digits a currency's smallest unit has.
 *
 * Amounts arrive from the API as whole minor units, and turning them back into
 * an amount means dividing by that currency's own scale. Dividing by a hundred
 * regardless is the bug this exists to prevent: a Shopee commission of 95,000
 * dong is stored as 95,000 and was being shown as ₫950, which is a hundred
 * times too small and entirely plausible on screen.
 *
 * Deliberately the same table as `services/api/src/trendrelay_api/money.py`.
 * The two ends have to agree about what a stored number means, and the only
 * thing worse than one wrong scale is two that differ.
 */

/** Currencies whose smallest unit is not a hundredth, by ISO 4217. */
const MINOR_UNIT_DIGITS: Record<string, number> = {
  BIF: 0, CLP: 0, DJF: 0, GNF: 0, ISK: 0, JPY: 0, KMF: 0,
  KRW: 0, PYG: 0, RWF: 0, UGX: 0, UYI: 0, VND: 0, VUV: 0,
  XAF: 0, XOF: 0, XPF: 0,
  BHD: 3, IQD: 3, JOD: 3, KWD: 3, LYD: 3, OMR: 3, TND: 3,
};

/**
 * Two, for anything not listed.
 *
 * Right for almost every currency, and being wrong is a factor of a hundred
 * either way - so guessing zero for an unknown code would misstate far more
 * currencies than it rescued.
 */
export const DEFAULT_MINOR_UNIT_DIGITS = 2;

export function minorUnitDigits(currency: string): number {
  return MINOR_UNIT_DIGITS[(currency || "").trim().toUpperCase()] ?? DEFAULT_MINOR_UNIT_DIGITS;
}

/** A stored amount, back as the number somebody would say out loud. */
export function fromMinorUnits(value: number, currency: string): number {
  return value / 10 ** minorUnitDigits(currency);
}
