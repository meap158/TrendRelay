"use client";

import { useEffect, useState } from "react";

import { useLocale } from "../i18n-provider";
import type { LibraryAsset } from "../publish/composer";
import {
  rememberLibrary,
  type FacetVocabulary,
  type LibraryRecipe,
  type RecipeExamples,
} from "../../lib/recipe-library";

type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;
type Catalogue = { recipes: LibraryRecipe[]; facets: FacetVocabulary };

/**
 * One fetch of the recipe catalogue per workspace, shared by every surface.
 *
 * The Generate dialog, the Library's recipe filter and the product table all
 * name recipes, and the mined ones are named only by this data. Fetched in
 * each, they disagreed for as long as one had loaded and another had not - a
 * table row showing an id while the dialog beside it showed a name. The
 * catalogue changes only when a batch ships, so the first answer is kept.
 */
const catalogues = new Map<string, Promise<Catalogue>>();

function loadCatalogue(workspaceId: string, apiFetch: Fetcher): Promise<Catalogue> {
  let pending = catalogues.get(workspaceId);
  if (!pending) {
    pending = apiFetch(`/api/workspaces/${workspaceId}/attribution/creative-recipes`)
      .then(async (response): Promise<Catalogue> => {
        if (!response.ok) throw new Error(String(response.status));
        const payload = await response.json() as Partial<Catalogue>;
        return {
          recipes: Array.isArray(payload.recipes) ? payload.recipes : [],
          facets: payload.facets ?? {},
        };
      })
      .catch((error) => {
        // A failed load is not cached: the next surface to ask tries again.
        catalogues.delete(workspaceId);
        throw error;
      });
    catalogues.set(workspaceId, pending);
  }
  return pending;
}

export function useRecipeLibrary(workspaceId: string | null | undefined, apiFetch: Fetcher) {
  const { locale } = useLocale();
  const [catalogue, setCatalogue] = useState<Catalogue>({ recipes: [], facets: {} });
  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    loadCatalogue(workspaceId, apiFetch)
      .then((loaded) => {
        if (cancelled) return;
        rememberLibrary(loaded.recipes, locale);
        setCatalogue(loaded);
      })
      // The hand-written recipes still work without it; nothing to report.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workspaceId, apiFetch, locale]);
  return catalogue;
}

/**
 * What each recipe has made so far, fetched while `active` - the Generate
 * dialog being open - and again each time it opens, since a finished draft
 * adds files between two openings.
 */
export function useRecipeExamples(
  workspaceId: string | null | undefined,
  apiFetch: Fetcher,
  active: boolean,
): RecipeExamples<LibraryAsset> {
  const [examples, setExamples] = useState<RecipeExamples<LibraryAsset>>({});
  useEffect(() => {
    if (!workspaceId || !active) return;
    let cancelled = false;
    void apiFetch(`/api/workspaces/${workspaceId}/attribution/creative-recipes/examples`)
      .then(async (response) => (response.ok
        ? (await response.json() as { examples?: RecipeExamples<LibraryAsset> }).examples ?? {}
        : {}))
      .then((found) => {
        if (!cancelled) setExamples(found);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [workspaceId, apiFetch, active]);
  return examples;
}
