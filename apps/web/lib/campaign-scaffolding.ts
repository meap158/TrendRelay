/**
 * The caption scaffolding a campaign starts with, in its own language.
 *
 * The disclosure leads every caption on every network and is not optional -
 * each post is its own advertisement - and the profile-link wording stands in
 * where a link in a post is not clickable. Both follow the campaign's post
 * language, so a Vietnamese campaign does not open with an English disclosure
 * that somebody has to notice and fix.
 *
 * The API has always written these at creation. What it could not do is show
 * them to whoever is filling in the form, which meant the two fields were
 * invisible until the campaign existed and its settings were reopened. This is
 * the same table, so the form can offer the same wording the server would have
 * chosen, and let it be edited before the campaign is made rather than after.
 *
 * Duplicated deliberately, and guarded: `campaign-scaffolding.test.ts` reads
 * the Python table and fails if these drift apart. An endpoint would have
 * avoided the copy at the cost of a round trip before the dialog could render
 * its defaults, which is a worse trade for seven short strings that change
 * about never.
 */

import type { Locale } from "./i18n/locales.ts";

export type Scaffolding = { disclosure: string; bioHint: string };

export const CAMPAIGN_SCAFFOLDING: Record<Locale, Scaffolding> = {
  en: {
    disclosure: "Affiliate link; we may earn a commission.",
    bioHint: "Link in bio",
  },
  vi: {
    disclosure: "Liên kết tiếp thị; chúng tôi có thể nhận hoa hồng.",
    bioHint: "Link ở tiểu sử",
  },
  ja: {
    disclosure: "アフィリエイトリンクです。報酬を得る場合があります。",
    bioHint: "プロフィールのリンク",
  },
  fr: {
    disclosure: "Lien affilié ; nous pouvons percevoir une commission.",
    bioHint: "Lien en bio",
  },
  zh: {
    disclosure: "推广链接，我们可能会获得佣金。",
    bioHint: "链接在简介",
  },
  ru: {
    disclosure: "Партнёрская ссылка; мы можем получить комиссию.",
    bioHint: "Ссылка в профиле",
  },
  ar: {
    disclosure: "رابط تسويق بالعمولة؛ قد نحصل على عمولة.",
    bioHint: "الرابط في الملف الشخصي",
  },
};

/** The scaffolding for one language, falling back to English as the API does. */
export function scaffoldingFor(language: string): Scaffolding {
  return CAMPAIGN_SCAFFOLDING[language as Locale] ?? CAMPAIGN_SCAFFOLDING.en;
}

/**
 * Whether a field still holds wording this app chose, in any language.
 *
 * What decides if changing the post language may rewrite it. Checking against
 * every language rather than only the previous one matters because the form
 * can be switched several times before it is submitted: en -> vi -> fr should
 * end in French, and comparing only against the language most recently left
 * would stop following after the first change.
 *
 * Empty counts as untouched, so clearing the field and picking a language
 * fills it again rather than leaving a campaign with no disclosure at all.
 */
export function isDefaultScaffolding(field: keyof Scaffolding, value: string): boolean {
  const text = value.trim();
  if (!text) return true;
  return Object.values(CAMPAIGN_SCAFFOLDING).some((entry) => entry[field] === text);
}
