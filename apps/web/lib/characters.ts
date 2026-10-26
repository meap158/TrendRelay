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

/**
 * Recipes that show their own recurring model (Hikaru) when no character is
 * chosen. They take a character like any other recipe: the chosen one takes
 * her place in the wording, so the composer only says so rather than locking
 * the choice.
 */
export const RECIPES_WITH_DEFAULT_MODEL: readonly string[] = ["hikaru_lookbook", "hikaru_walk"];

export function recipeHasDefaultModel(recipe: string): boolean {
  return RECIPES_WITH_DEFAULT_MODEL.includes(recipe);
}

export const RECIPES_REQUIRING_CHARACTER: readonly string[] = [
  "character_editorial_carousel",
];

export function recipeRequiresCharacter(recipe: string): boolean {
  return RECIPES_REQUIRING_CHARACTER.includes(recipe);
}

/** Every recipe can feature a Library character. */
export function recipeTakesCharacter(_recipe: string): boolean {
  return true;
}

/** The Library's source filter for character pictures. */
export const CHARACTER_SOURCE = "character";

/**
 * The character Generate media starts with for these products: the default of
 * the campaigns they are tagged to, when those campaigns agree on one.
 *
 * Campaigns with no default do not count against it, so a product tagged to a
 * brand's sleepwear campaign and to a general shop campaign still starts with
 * the sleepwear model. Two different defaults among the selection start with
 * none, since neither is the one somebody meant.
 */
export function campaignDefaultCharacter(
  products: readonly { offers: readonly { id: string }[] }[],
  campaignsByOffer: Readonly<Record<string, readonly string[]>>,
  campaigns: readonly { id: string; default_character_id?: string | null }[],
): string {
  const defaults = new Map(campaigns.map((campaign) => [campaign.id, campaign.default_character_id ?? ""]));
  const chosen = new Set<string>();
  for (const product of products) {
    for (const offer of product.offers) {
      for (const campaignId of campaignsByOffer[offer.id] ?? []) {
        const character = defaults.get(campaignId);
        if (character) chosen.add(character);
      }
    }
  }
  return chosen.size === 1 ? [...chosen][0] : "";
}
