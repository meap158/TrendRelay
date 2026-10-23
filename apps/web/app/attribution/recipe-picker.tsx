"use client";

import { useMemo, useState } from "react";

import { useLocale, useT } from "../i18n-provider";
import { AssetThumbnail, type LibraryAsset } from "../publish/composer";
import { Button } from "../ui/button";
import { ChoiceGroup } from "../ui/choice-group";
import { RecipePreview } from "./recipe-previews";
import {
  facetChips,
  filterRecipes,
  localized,
  recipeMakes,
  searchRecipes,
  type FacetSelection,
  type FacetVocabulary,
  type LibraryRecipe,
  type RecipeExamples,
  type RecipeFacet,
} from "../../lib/recipe-library";

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

/**
 * The chip rows, and what each is called. Format is the kind control above
 * the picker, so it has no row. A row whose recipes in view share one value
 * is not drawn: a single chip filters nothing.
 */
const FACET_ROWS: readonly { facet: RecipeFacet; label: string }[] = [
  { facet: "layout", label: "attribution.generate.recipeFacetLayout" },
  { facet: "pose", label: "attribution.generate.recipeFacetPose" },
  { facet: "setting", label: "attribution.generate.recipeFacetSetting" },
  { facet: "framing", label: "attribution.generate.recipeFacetFraming" },
  { facet: "mood", label: "attribution.generate.recipeFacetMood" },
  { facet: "cast", label: "attribution.generate.recipeFacetCast" },
  { facet: "products", label: "attribution.generate.recipeFacetProducts" },
];

/**
 * The recipe column of the Generate dialog: search, filter chips, and a grid
 * of recipe cards, each with what that recipe has already made.
 *
 * With forty-odd recipes the column is a gallery, not a list. Search and the
 * chips narrow it; the chips sit in a panel that folds away so the cards keep
 * the height, and the choices already made stay visible as removable chips
 * when it is folded. The chosen recipe follows the filter: a card the filter
 * hides must not stay the one that gets queued.
 */
export function RecipePicker({
  kind,
  value,
  onChange,
  locked,
  recipes,
  fallback,
  vocabulary,
  examples,
  labelOf,
  helpOf,
  workspaceId,
  apiFetch,
}: {
  kind: string;
  value: string;
  onChange: (recipe: string) => void;
  locked: boolean;
  /** The whole catalogue, hand-written and mined, from `useRecipeLibrary`. */
  recipes: readonly LibraryRecipe[];
  /** The hand-written ids of this kind, for before the catalogue arrives. */
  fallback: readonly string[];
  vocabulary: FacetVocabulary;
  examples: RecipeExamples<LibraryAsset>;
  labelOf: (recipe: string) => string;
  helpOf: (recipe: string) => string;
  workspaceId: string | null;
  apiFetch: Fetcher;
}) {
  const t = useT();
  const { locale } = useLocale();
  const [query, setQuery] = useState("");
  const [choice, setChoice] = useState<FacetSelection>({});
  const [filtersOpen, setFiltersOpen] = useState(false);
  // A kind change starts the search over: the old filters were for other cards.
  const [filteredKind, setFilteredKind] = useState(kind);
  if (filteredKind !== kind) {
    setFilteredKind(kind);
    setQuery("");
    setChoice({});
  }

  const ofKind = useMemo(
    () => recipes.filter((recipe) => recipeMakes(recipe, kind)),
    [recipes, kind],
  );
  const textOf = (recipe: LibraryRecipe) => `${labelOf(recipe.id)} ${helpOf(recipe.id)}`;
  const shownFor = (nextQuery: string, nextChoice: FacetSelection) => {
    if (ofKind.length === 0) return nextQuery || Object.values(nextChoice).some(Boolean) ? [] : [...fallback];
    return filterRecipes(searchRecipes(ofKind, nextQuery, textOf), nextChoice).map((recipe) => recipe.id);
  };
  const shown = shownFor(query, choice);
  const active = Object.values(choice).filter(Boolean).length;

  function narrow(nextQuery: string, nextChoice: FacetSelection) {
    setQuery(nextQuery);
    setChoice(nextChoice);
    const visible = shownFor(nextQuery, nextChoice);
    if (visible.length > 0 && !visible.includes(value)) onChange(visible[0]);
  }

  function toggle(facet: RecipeFacet, key: string) {
    narrow(query, { ...choice, [facet]: choice[facet] === key || !key ? "" : key });
  }

  const rows = FACET_ROWS.map(({ facet, label }) => ({
    facet,
    label,
    chips: facetChips(ofKind, facet, choice, vocabulary, locale, t("attribution.generate.recipeFilterAny")),
  })).filter((row) => row.chips.length > 2 || choice[row.facet]);

  const chosen = FACET_ROWS.flatMap(({ facet }) => {
    const key = choice[facet];
    if (!key) return [];
    return [{ facet, key, label: localized(vocabulary[facet]?.[key], locale) || key }];
  });

  return (
    <div className="recipe-picker">
      {!locked && ofKind.length > 0 && (
        <div className="recipe-picker-bar">
          <input
            type="search"
            className="recipe-picker-search"
            value={query}
            placeholder={t("attribution.generate.recipeSearch")}
            aria-label={t("attribution.generate.recipeSearch")}
            onChange={(event) => narrow(event.target.value, choice)}
          />
          <Button
            variant={filtersOpen || active ? "secondary" : "quiet"}
            size="sm"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((open) => !open)}
          >
            {t("attribution.generate.recipeFilters")}{active ? ` · ${active}` : ""}
          </Button>
          <small className="recipe-picker-count" role="status">
            {t("attribution.generate.recipeCount", { count: shown.length })}
          </small>
          {!filtersOpen && chosen.length > 0 && (
            <div className="recipe-picker-chosen">
              {chosen.map(({ facet, key, label }) => (
                <button
                  key={facet}
                  type="button"
                  className="filter-chip selected"
                  onClick={() => toggle(facet, key)}
                >
                  {label} <span aria-hidden="true">×</span>
                </button>
              ))}
            </div>
          )}
          {filtersOpen && (
            <div className="recipe-picker-filters" role="group" aria-label={t("attribution.generate.recipeFilters")}>
              {rows.map(({ facet, label, chips }) => (
                <div key={facet} className="recipe-picker-facet">
                  <span>{t(label)}</span>
                  <div className="recipe-picker-chips">
                    {chips.map((chip) => (
                      <button
                        key={chip.key || "any"}
                        type="button"
                        aria-pressed={(choice[facet] ?? "") === chip.key}
                        className={`filter-chip${(choice[facet] ?? "") === chip.key ? " selected" : ""}`}
                        onClick={() => toggle(facet, chip.key)}
                      >
                        {chip.label}
                        <span className="filter-chip-count">{chip.count}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              {active > 0 && (
                <Button variant="quiet" size="sm" onClick={() => narrow(query, {})}>
                  {t("attribution.generate.recipeFilterClear")}
                </Button>
              )}
            </div>
          )}
        </div>
      )}
      {!locked && shown.length === 0 && (
        <p className="generate-blocker">{t("attribution.generate.recipeFilterNone")}</p>
      )}
      <ChoiceGroup
        name="generate-recipe"
        legend={t("attribution.generate.recipe")}
        layout="cards"
        value={value}
        disabled={locked}
        options={(locked ? [value] : shown).map((option) => {
          const row = recipes.find((item) => item.id === option);
          const made = examples[option];
          return {
            value: option,
            label: labelOf(option),
            description: helpOf(option),
            media: <RecipePreview recipe={option} mined={row?.builtin ? null : row} />,
            meta: made && made.count > 0 && workspaceId ? (
              <span className="recipe-made">
                <span className="recipe-made-thumbs">
                  {made.assets.slice(0, 3).map((asset) => (
                    <AssetThumbnail
                      key={asset.id}
                      asset={asset}
                      workspaceId={workspaceId}
                      apiFetch={apiFetch}
                      hoverPreview
                    />
                  ))}
                </span>
                <small>{t("attribution.generate.recipeMade", { count: made.count })}</small>
              </span>
            ) : undefined,
          };
        })}
        onChange={onChange}
      />
      <small className="recipe-picker-key">{t("attribution.generate.recipePreviewKey")}</small>
    </div>
  );
}
