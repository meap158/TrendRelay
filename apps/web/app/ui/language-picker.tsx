"use client";

/**
 * Choosing a language.
 *
 * The same `SearchSelect` the zone picker beside it uses. This was a native
 * `<select>`, and the argument for it was a good one: keyboard navigation and
 * screen-reader correctness for free, the platform's own picker on a phone, and
 * it flips with the document direction without any work.
 *
 * What outweighed it was sitting next to a control that could not be native.
 * Four hundred timezones are a list you search, not one you scroll, so that one
 * had to be the app's own component - and two adjacent dropdowns that do not
 * look alike read as two different kinds of thing. The trade is real rather than
 * free: the platform picker on a phone was worth having.
 *
 * The search box is switched off here. Seven languages are a list you read, and
 * a field asking to be typed in stands in front of an answer already on screen.
 *
 * Each language is still named in itself - somebody looking for Japanese scans
 * for 日本語, not for the word "Japanese".
 */

import { LOCALES, Locale, isLocale } from "../../lib/i18n/locales";
import { useLocale } from "../i18n-provider";
import { SearchSelect } from "./search-select";

export function LanguagePicker({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, t } = useLocale();

  return (
    <label className={`toolbar-picker language-picker${compact ? " compact" : ""}`}>
      {/* The label is for assistive technology even when the text is hidden:
          an unlabelled control announces only its current value. */}
      <span>{t("nav.language")}</span>
      <SearchSelect
        value={locale}
        // No search box: seven languages are a list you read, and a field
        // asking to be typed in is in the way of an answer already on screen.
        searchable={false}
        placeholder={t("nav.chooseLanguage")}
        options={LOCALES.map((item) => ({ value: item.code, label: item.label }))}
        onChange={(next) => { if (isLocale(next)) setLocale(next as Locale); }}
      />
    </label>
  );
}
