"use client";

/**
 * Choosing a language.
 *
 * A native `<select>` rather than a custom menu: it is keyboard-navigable and
 * screen-reader-correct for free, it opens as the platform's own picker on a
 * phone, and it flips with the document direction without any work. A prettier
 * custom dropdown would be worse at every one of those.
 *
 * Each language is named in itself — someone looking for Japanese scans for
 * 日本語, not for the word "Japanese".
 */

import { LOCALES, Locale, isLocale } from "../../lib/i18n/locales";
import { useLocale } from "../i18n-provider";

const SR_ONLY: React.CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
};

export function LanguagePicker({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, t } = useLocale();

  return (
    <label
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        color: "#5f6368",
      }}
    >
      {/* The label is for assistive technology even when the text is hidden:
          an unlabelled select announces only its current value. */}
      <span style={compact ? SR_ONLY : undefined}>{t("nav.language")}</span>
      <select
        value={locale}
        aria-label={t("nav.chooseLanguage")}
        onChange={(event) => {
          const next = event.target.value;
          if (isLocale(next)) setLocale(next as Locale);
        }}
        style={{
          borderRadius: 6,
          border: "1px solid #dadce0",
          padding: "4px 6px",
          background: "#fff",
          color: "#1c2b33",
          font: "inherit",
          fontSize: 12,
        }}
      >
        {LOCALES.map((item) => (
          <option key={item.code} value={item.code}>
            {item.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Visible to a screen reader, not to the eye. */
