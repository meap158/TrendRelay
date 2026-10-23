/**
 * The mined recipe library, as the API's `/attribution/creative-recipes`
 * returns it, and the filtering the recipe picker's chips do over it.
 *
 * The hand-written recipes are known to the dialog already. These arrive as
 * data, with their names in every locale, because the library grows a batch
 * at a time (SOP/attribution/mine-reference-recipes.md) and a hard-coded copy
 * of it here would fall behind the first time a batch landed.
 */

export type LocalizedText = Record<string, string>;

export const RECIPE_FACETS = [
  "format",
  "cast",
  "products",
  "pose",
  "framing",
  "setting",
  "mood",
  "layout",
] as const;
export type RecipeFacet = (typeof RECIPE_FACETS)[number];

export type LibraryRecipe = {
  id: string;
  kind: "image" | "carousel" | "video";
  card_count: number | null;
  max_products: number;
  cast: "person" | "none";
  together: "card" | "frame";
  requires_character: boolean;
  facets: Record<RecipeFacet, string[]>;
  label: LocalizedText;
  help: LocalizedText;
  preview: string;
};

export type FacetVocabulary = Partial<Record<RecipeFacet, Record<string, LocalizedText>>>;

/** One choice per facet; an empty string is "any". */
export type FacetSelection = Partial<Record<RecipeFacet, string>>;

/** The text for this locale, then English, then the first one there is. */
export function localized(texts: LocalizedText | undefined, locale: string): string {
  if (!texts) return "";
  return texts[locale] || texts.en || Object.values(texts)[0] || "";
}

/** Whether a recipe has every chosen facet value. */
export function matchesFacets(recipe: LibraryRecipe, selection: FacetSelection): boolean {
  return RECIPE_FACETS.every((facet) => {
    const wanted = selection[facet];
    return !wanted || (recipe.facets[facet] ?? []).includes(wanted);
  });
}

export function filterRecipes(
  recipes: readonly LibraryRecipe[],
  selection: FacetSelection,
): LibraryRecipe[] {
  return recipes.filter((recipe) => matchesFacets(recipe, selection));
}

/**
 * The chips for one facet: "any" first, then every value some recipe in view
 * carries, with how many recipes would remain if it were chosen alongside the
 * other facets' choices. A value no recipe has is left out rather than shown
 * as a dead end; the value already chosen always stays, so it can be undone.
 */
export function facetChips(
  recipes: readonly LibraryRecipe[],
  facet: RecipeFacet,
  selection: FacetSelection,
  vocabulary: FacetVocabulary,
  locale: string,
  anyLabel: string,
): { key: string; label: string; count: number }[] {
  const others = { ...selection, [facet]: "" };
  const pool = filterRecipes(recipes, others);
  const counts = new Map<string, number>();
  for (const recipe of pool) {
    for (const value of recipe.facets[facet] ?? []) {
      counts.set(value, (counts.get(value) ?? 0) + 1);
    }
  }
  const chosen = selection[facet];
  if (chosen && !counts.has(chosen)) counts.set(chosen, 0);
  const words = vocabulary[facet] ?? {};
  // Vocabulary order, so the chips stand in the same place every time.
  const order = [...Object.keys(words), ...[...counts.keys()].filter((key) => !(key in words))];
  return [
    { key: "", label: anyLabel, count: pool.length },
    ...order
      .filter((key) => counts.has(key))
      .map((key) => ({ key, label: localized(words[key], locale) || key, count: counts.get(key) ?? 0 })),
  ];
}

let known: LibraryRecipe[] = [];
let knownLocale = "en";

/**
 * What the last fetch returned, for labels drawn outside the dialog (a
 * draft's row in the product table). Those surfaces show a stored recipe id
 * and would otherwise fall back to a hand-written recipe's name.
 */
export function rememberLibrary(recipes: LibraryRecipe[], locale: string) {
  known = recipes;
  knownLocale = locale;
}

export function libraryRecipeLabel(id: string, locale?: string): string | null {
  const found = known.find((recipe) => recipe.id === id);
  return found ? localized(found.label, locale ?? knownLocale) : null;
}

/** "sunlit_window_lookbook" -> "Sunlit window lookbook", for an id nothing has named yet. */
export function humanizeRecipeId(id: string): string {
  const words = id.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : id;
}
