/**
 * Characters as the Generate dialog and the Library read them.
 *
 * The rules a component would otherwise bury: which characters can be
 * featured, when the list is worth asking for again, what a draft request
 * carries, and how many paid requests drawing one costs.
 */

export type CharacterStatus = "generating" | "ready" | "failed";

export type Character = {
  id: string;
  slug: string;
  name: string;
  appearance: string;
  outfit: string;
  builtin: boolean;
  status: CharacterStatus;
  error: string | null;
  portrait_asset_id: string | null;
  sheet_asset_id: string | null;
  archived?: boolean;
};

/** What a stored draft says it featured. */
export type DraftCharacter = {
  id: string;
  name: string;
  portrait_asset_id: string | null;
  sheet_asset_id: string | null;
  archived?: boolean;
};

export type CharacterGenerator = {
  ready: boolean;
  provider_id: string;
  label: string;
  message: string;
};

/** A character can be featured once its portrait exists and nothing is still drawing. */
export function isPickable(character: Character): boolean {
  return character.status === "ready" && Boolean(character.portrait_asset_id);
}

/** Ask again while any character is still being drawn. */
export function anyDrawing(characters: readonly Character[]): boolean {
  return characters.some((character) => character.status === "generating");
}

/**
 * The draft request's share. Off, or on with nothing chosen, sends nothing,
 * which is what every draft without a character has always sent.
 */
export function characterField(enabled: boolean, characterId: string): { character_id?: string } {
  return enabled && characterId ? { character_id: characterId } : {};
}

/**
 * Paid image requests one new character costs: a portrait and a sheet, or
 * only the sheet when a Library picture is the portrait.
 */
export function paidRequests(fromReference: boolean): number {
  return fromReference ? 1 : 2;
}

/** Recipes that already bring their own recurring model cannot feature another. */
export const RECIPES_WITH_OWN_MODEL: readonly string[] = ["hikaru_lookbook", "hikaru_walk"];

export function recipeTakesCharacter(recipe: string): boolean {
  return !RECIPES_WITH_OWN_MODEL.includes(recipe);
}

/** The Library's source filter for character pictures. */
export const CHARACTER_SOURCE = "character";
