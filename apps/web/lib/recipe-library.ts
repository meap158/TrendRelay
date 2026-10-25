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
  /** A hand-written recipe: named by the dialog's own strings, no labels here. */
  builtin?: boolean;
  kind: "image" | "carousel" | "video";
  /** Every kind it can make; a hand-written recipe can make more than one. */
  kinds?: ("image" | "carousel" | "video")[];
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

/** What a recipe has already made: how many files, and the newest few. */
export type RecipeExamples<Asset> = Record<string, { count: number; assets: Asset[] }>;

/** Whether a recipe can make this kind. */
export function recipeMakes(recipe: LibraryRecipe, kind: string): boolean {
  return (recipe.kinds ?? [recipe.kind]).includes(kind as LibraryRecipe["kind"]);
}

/**
 * Recipes whose name or description holds every word of the query, in any
 * order and case. Both texts come from the caller, because a hand-written
 * recipe is named by translated strings this file does not have.
 */
export function searchRecipes(
  recipes: readonly LibraryRecipe[],
  query: string,
  textOf: (recipe: LibraryRecipe) => string,
): LibraryRecipe[] {
  const words = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...recipes];
  return recipes.filter((recipe) => {
    const text = textOf(recipe).toLocaleLowerCase();
    return words.every((word) => text.includes(word));
  });
}

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
  const found = known.find((recipe) => recipe.id === id && !recipe.builtin);
  return found ? localized(found.label, locale ?? knownLocale) || null : null;
}

/** "sunlit_window_lookbook" -> "Sunlit window lookbook", for an id nothing has named yet. */
export function humanizeRecipeId(id: string): string {
  const words = id.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : id;
}

/** The label of every recipe the API can store. */
const RECIPE_LABELS: Record<string, string> = {
  bed_flat_lay: "attribution.generate.recipeBed",
  mannequin_transition: "attribution.generate.recipeMannequin",
  mirror_selfie: "attribution.generate.recipeMirror",
  mannequin_walkaround: "attribution.generate.recipeWalkaround",
  styled_flat_lay: "attribution.generate.recipeStyledFlatLay",
  flat_lay_reveal: "attribution.generate.recipeFlatLayReveal",
  sleepwear_lookbook: "attribution.generate.recipeSleepwearLookbook",
  held_up_garment: "attribution.generate.recipeHeldUp",
  hikaru_lookbook: "attribution.generate.recipeHikaru",
  character_editorial_carousel: "attribution.generate.recipeCharacterEditorialCarousel",
  ceiling_fan_transition: "attribution.generate.recipeCeilingFan",
  hanger_rail_reveal: "attribution.generate.recipeHangerRail",
  hikaru_walk: "attribution.generate.recipeHikaruWalk",
};

/**
 * A recipe's name: a hand-written one through its translated string, a mined
 * one through the catalogue, and an id nothing has named yet as words. Kept
 * here, free of components, so filters and tables can import it without
 * pulling the Attribution page in after them.
 */
export function creativeRecipe(t: (path: string) => string, recipe: string): string {
  const key = RECIPE_LABELS[recipe];
  if (key) return t(key);
  // A mined recipe: its name came with the library, if the dialog has fetched
  // it this session, and otherwise the id itself reads well enough.
  return libraryRecipeLabel(recipe) ?? humanizeRecipeId(recipe);
}

/** Reuse the generator's translated descriptions on compact recipe surfaces. */
export function creativeRecipeHelp(t: (path: string) => string, recipe: string): string {
  const key = RECIPE_LABELS[recipe];
  return key ? t(`${key}Help`) : "";
}
