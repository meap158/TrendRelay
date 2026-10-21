---
id: creations.create-character
title: Create or revise a photorealistic character
version: 1
---
# Create or revise a photorealistic character

Use for a new cast member or an explicitly requested appearance revision. The default visual direction is beautiful professional fashion-model photography with believable human detail. Beauty and realism are both requirements.

## Define a distinct adult identity

Write a brief before generating. Record a name, explicit adult age, fictional provenance, face shape, cheek/jaw proportions, eyes/brows, nose/lips, skin tone, hair length/texture/part, build and baseline outfit. Vietnamese characters come first when requested. Do not use ethnicity as a substitute for an individual identity.

Differentiate every cast member on at least four visible dimensions, including facial structure; a new haircut or clothing color alone is insufficient. Review portraits side by side and reject same-face results. Current cast direction:

| Character | Face and presence | Hair | Skin/build | Baseline outfit |
| --- | --- | --- | --- | --- |
| Mai, 25 | Soft round-oval, fuller cheeks, petite chin, warm delicate expression | Long straight black, center part | Light-medium golden, graceful slim | Ivory blouse, taupe trousers |
| Thảo, 25 | Soft diamond, delicate high cheekbones, almond eyes, confident | Short espresso wavy bob, deep side part | Fair light neutral-ivory, athletic adult | Powder-blue tee, charcoal trousers |
| An, 26 | Heart-shaped oval, soft cheeks, rounded chin, fresh youthful adult expression | Black low ponytail, wispy side-swept strands | Fair light warm ivory, graceful adult | Sage blouse, navy trousers, silver pendant |
| Hà My, 27 | Balanced oval, refined cheekbones, fresh editorial expression | Long chocolate waves, side part | Warm ivory-beige, graceful slim | Ivory top, beige trousers |

Create fictional identities from text without real-person reference images. Never relabel a real-person-derived sheet as fictional. Record provenance with the assets and in character appearance.

## Generate portrait, then consistency sheet

Use the built-in imagegen skill. One call per asset. Start with a head-and-shoulders portrait: eye-level portrait lens feel, broad flattering soft light, neutral warm-grey backdrop, polished light editorial makeup and professionally styled hair. Keep subtle pores, realistic lashes, fine hairs, natural lip texture and slight asymmetry. Avoid plastic skin, uncanny symmetry, doll faces and exaggerated eyes. Do not force blemishes, roughness or aging as a shortcut to realism. Do not over-retouch away identity.

Inspect the portrait before making the sheet. Use only that generated portrait as the identity authority for the sheet. Preserve exact features, age, skin tone, hair and proportions. Use a neutral opaque everyday outfit. At minimum include front and both three-quarter faces, both profiles, back hair, neutral/smile expressions, and full-body front/three-quarter/side/back. Use consistent light/background and simple labels. Full figures must include feet and believable hands/anatomy. For more detailed production requests add the user's specified feature/outfit panels. Reject identity drift or incorrect angle labels.

Save originals and exact prompts under output/characters/<slug>-<version>/. Keep superseded files intact and record which version is current.

## Register and verify before delivery

Generated files alone do not make a character available in Attribution. POST /api/workspaces/{workspace_id}/characters/register/images with name, appearance, outfit, portrait_base64 and sheet_base64. Both images are imported into Library as source_type character; the character becomes ready without another generation-provider call. Existing Library images can use /characters/register with portrait_asset_id and sheet_asset_id.

For an authorized revision, include replace_character_id to update that existing entry rather than making duplicate names. Keep the same character ID; retain previous Library assets and the before/after receipt. New visuals affect future reference use; already generated images are not retroactively replaced.

Verify GET /characters returns the exact ID, ready status and new portrait/sheet IDs. Verify both Library assets exist at original resolution with source_type character. Attribution's Generate a creative picker consumes this feed; ensure both previews are available. Do not claim registration based only on an upload queue. Record IDs, prompts, QA and receipts in AGENT_HANDOVER.md.
