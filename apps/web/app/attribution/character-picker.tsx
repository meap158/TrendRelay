"use client";

import { useCallback, useEffect, useState } from "react";

import { useT } from "../i18n-provider";
import { type LibraryAsset, MediaPicker, useAssetPoster } from "../publish/composer";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import {
  anyDrawing,
  isPickable,
  paidRequests,
  recipeTakesCharacter,
  type Character,
  type CharacterGenerator,
  type DraftCharacter,
} from "../../lib/characters";
import { opaquePreviewUrl } from "../../lib/media-preview";

type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;

/** How often the list is read again while a character is being drawn. */
const DRAWING_POLL_MS = 4000;

async function detail(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    // A refusal without JSON still needs a sentence.
  }
  return fallback;
}

/** A Library image at full size, as an object URL. "" while it loads. */
function useAssetOriginal(assetId: string | null, workspaceId: string, apiFetch: Fetcher): string {
  const [loaded, setLoaded] = useState<{ id: string; url: string } | null>(null);
  useEffect(() => {
    if (!assetId) return;
    let active = true;
    let objectUrl = "";
    apiFetch(opaquePreviewUrl(`/api/workspaces/${workspaceId}/media/library/assets/${assetId}/content/original`))
      .then((response) => {
        if (!response.ok) throw new Error("unavailable");
        return response.arrayBuffer();
      })
      .then((bytes) => {
        objectUrl = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
        if (active) setLoaded({ id: assetId, url: objectUrl });
        else URL.revokeObjectURL(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiFetch, assetId, workspaceId]);
  return loaded && loaded.id === assetId ? loaded.url : "";
}

function Portrait({ assetId, workspaceId, apiFetch }: {
  assetId: string | null;
  workspaceId: string;
  apiFetch: Fetcher;
}) {
  const source = useAssetPoster(assetId, workspaceId, apiFetch);
  return (
    <span className="character-portrait" aria-hidden="true">
      {/* eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL */}
      {source ? <img src={source} alt="" /> : null}
    </span>
  );
}

function SheetViewer({ character, workspaceId, apiFetch, onClose }: {
  character: { name: string; sheet_asset_id: string | null; portrait_asset_id: string | null } | null;
  workspaceId: string;
  apiFetch: Fetcher;
  onClose: () => void;
}) {
  const t = useT();
  const sheet = useAssetOriginal(character?.sheet_asset_id ?? null, workspaceId, apiFetch);
  const portrait = useAssetOriginal(character?.portrait_asset_id ?? null, workspaceId, apiFetch);
  return (
    <Dialog
      className="character-sheet-dialog"
      size="wide"
      open={character !== null}
      title={character ? t("attribution.characters.sheetTitle", { name: character.name }) : ""}
      onClose={onClose}
    >
      <div className="character-sheet-view">
        {/* eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL */}
        {portrait ? <img src={portrait} alt="" /> : null}
        {character?.sheet_asset_id
          // eslint-disable-next-line @next/next/no-img-element -- authenticated blob URL
          ? (sheet ? <img src={sheet} alt="" /> : <p>{t("attribution.characters.loading")}</p>)
          : <p>{t("attribution.characters.sheetMissing")}</p>}
      </div>
    </Dialog>
  );
}

function NewCharacterDialog({ open, workspaceId, apiFetch, generator, onClose, onCreated, onPickerChange }: {
  open: boolean;
  workspaceId: string;
  apiFetch: Fetcher;
  generator: CharacterGenerator | null;
  onClose: () => void;
  onCreated: (character: Character) => void;
  onPickerChange: (open: boolean) => void;
}) {
  const t = useT();
  const [name, setName] = useState("");
  const [appearance, setAppearance] = useState("");
  const [outfit, setOutfit] = useState("");
  const [reference, setReference] = useState<LibraryAsset | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function openPicker(next: boolean) {
    setPickerOpen(next);
    onPickerChange(next);
  }

  const ready = Boolean(generator?.ready);
  const complete = name.trim() !== "" && (reference !== null || appearance.trim() !== "");

  async function draw() {
    if (!generator || !complete) return;
    const confirmed = window.confirm(t("attribution.characters.confirmDraw", {
      count: paidRequests(reference !== null),
      provider: generator.label,
    }));
    if (!confirmed) return;
    setBusy(true);
    setError("");
    try {
      const response = await apiFetch(`/api/workspaces/${workspaceId}/characters`, {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          appearance: appearance.trim() || null,
          outfit: outfit.trim() || null,
          reference_asset_id: reference?.id ?? null,
          confirm_external_action: true,
        }),
      });
      if (!response.ok) {
        setError(await detail(response, t("attribution.generate.requestFailed")));
        return;
      }
      const payload = await response.json() as { character?: Character };
      if (payload.character) onCreated(payload.character);
      setName("");
      setAppearance("");
      setOutfit("");
      setReference(null);
      onClose();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Dialog
        className="character-new-dialog"
        open={open}
        title={t("attribution.characters.newTitle")}
        description={t("attribution.characters.newHelp")}
        onClose={() => {
          if (pickerOpen) return;
          onClose();
        }}
        suspendDismiss={pickerOpen}
        footer={(
          <>
            <Button variant="quiet" onClick={onClose}>{t("attribution.characters.cancel")}</Button>
            <Button
              variant="primary"
              busy={busy}
              disabled={!ready || !complete || busy}
              onClick={() => void draw()}
            >{busy ? t("attribution.characters.drawing") : t("attribution.characters.draw")}</Button>
          </>
        )}
      >
        <div className="campaign-dialog-form character-new-form">
          {generator && !generator.ready && <p className="generate-blocker">{generator.message}</p>}
          <label>
            {t("attribution.characters.name")}
            <input value={name} maxLength={80} onChange={(event) => setName(event.target.value)} />
          </label>
          <div className="character-reference">
            <strong>{t("attribution.characters.fromLibrary")}</strong>
            <p>{t("attribution.characters.fromLibraryHelp")}</p>
            {reference ? (
              <span className="character-reference-chosen">
                <span>{t("attribution.characters.referenceChosen", { title: reference.title })}</span>
                <Button variant="quiet" size="sm" onClick={() => setReference(null)}>
                  {t("attribution.characters.clearReference")}
                </Button>
              </span>
            ) : (
              <Button variant="secondary" size="sm" onClick={() => openPicker(true)}>
                {t("attribution.characters.chooseReference")}
              </Button>
            )}
          </div>
          <label>
            {t("attribution.characters.appearance")}
            <textarea
              rows={3}
              maxLength={1000}
              value={appearance}
              placeholder={t("attribution.characters.appearancePlaceholder")}
              onChange={(event) => setAppearance(event.target.value)}
            />
          </label>
          <label>
            {t("attribution.characters.outfit")}
            <input
              value={outfit}
              maxLength={500}
              placeholder={t("attribution.characters.outfitPlaceholder")}
              onChange={(event) => setOutfit(event.target.value)}
            />
          </label>
          {error && <p role="alert">{error}</p>}
        </div>
      </Dialog>
      <MediaPicker
        open={pickerOpen}
        workspaceId={workspaceId}
        apiFetch={apiFetch}
        mediaKind="image"
        capacity={1}
        chosen={reference ? [reference.id] : []}
        pathOf={(asset) => asset.id}
        title={t("attribution.characters.referencePickerTitle")}
        description={t("attribution.characters.referencePickerHelp")}
        onPick={(asset) => {
          setReference(asset);
          openPicker(false);
        }}
        onClose={() => openPicker(false)}
      />
    </>
  );
}

/**
 * The Character row of "What generation uses".
 *
 * Off by default. On, it lists the workspace's characters as portrait cards;
 * the chosen one's portrait and sheet go to the provider and its description
 * joins the prompt. A stored draft shows what it featured instead.
 */
export function CharacterUse({
  workspaceId,
  apiFetch,
  locked,
  recipe,
  enabled,
  characterId,
  stored,
  onEnabledChange,
  onCharacterChange,
  onOverlayChange,
}: {
  workspaceId: string;
  apiFetch: Fetcher;
  /** A queued or reviewed draft: show, do not change. */
  locked: boolean;
  recipe: string;
  enabled: boolean;
  characterId: string;
  /** What a stored draft featured. Undefined while composing a new ask. */
  stored?: DraftCharacter | null;
  onEnabledChange: (enabled: boolean) => void;
  onCharacterChange: (characterId: string) => void;
  /** A dialog of this row is open, so the Generate dialog must not close. */
  onOverlayChange: (open: boolean) => void;
}) {
  const t = useT();
  const [characters, setCharacters] = useState<Character[]>([]);
  const [generator, setGenerator] = useState<CharacterGenerator | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [viewing, setViewing] = useState<DraftCharacter | Character | null>(null);
  const [creating, setCreating] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [busyId, setBusyId] = useState("");

  const overlay = viewing !== null || creating || pickerOpen;
  useEffect(() => {
    onOverlayChange(overlay);
  }, [overlay, onOverlayChange]);

  const load = useCallback(async () => {
    try {
      const response = await apiFetch(`/api/workspaces/${workspaceId}/characters`);
      if (!response.ok) {
        setError(await detail(response, t("attribution.generate.requestFailed")));
        return;
      }
      const payload = await response.json() as {
        characters?: Character[];
        generator?: CharacterGenerator;
      };
      setCharacters(payload.characters ?? []);
      setGenerator(payload.generator ?? null);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("attribution.generate.requestFailed"));
    } finally {
      setLoading(false);
    }
  }, [apiFetch, t, workspaceId]);

  const composing = stored === undefined;
  useEffect(() => {
    if (!composing || !enabled) return;
    let cancelled = false;
    void (async () => {
      if (!cancelled) await load();
    })();
    return () => {
      cancelled = true;
    };
  }, [composing, enabled, load]);

  const drawing = anyDrawing(characters);
  useEffect(() => {
    if (!composing || !enabled || !drawing) return;
    const timer = window.setInterval(() => void load(), DRAWING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [composing, drawing, enabled, load]);

  // The chosen character went away (removed, or its portrait deleted).
  const chosen = characters.find((item) => item.id === characterId) ?? null;
  useEffect(() => {
    if (characterId && !loading && composing && (!chosen || !isPickable(chosen))) {
      onCharacterChange("");
    }
  }, [characterId, chosen, composing, loading, onCharacterChange]);

  async function retry(character: Character) {
    if (!generator) return;
    if (!window.confirm(t("attribution.characters.confirmRetry", { provider: generator.label }))) return;
    setBusyId(character.id);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/characters/${character.id}/retry`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
      );
      if (!response.ok) setError(await detail(response, t("attribution.generate.requestFailed")));
      await load();
    } finally {
      setBusyId("");
    }
  }

  async function remove(character: Character) {
    if (!window.confirm(t("attribution.characters.confirmRemove", { name: character.name }))) return;
    setBusyId(character.id);
    try {
      const response = await apiFetch(
        `/api/workspaces/${workspaceId}/characters/${character.id}`,
        { method: "DELETE" },
      );
      if (!response.ok) setError(await detail(response, t("attribution.generate.requestFailed")));
      if (character.id === characterId) onCharacterChange("");
      await load();
    } finally {
      setBusyId("");
    }
  }

  const viewer = (
    <SheetViewer
      character={viewing}
      workspaceId={workspaceId}
      apiFetch={apiFetch}
      onClose={() => setViewing(null)}
    />
  );

  if (!composing) {
    return (
      <div className="generate-use">
        <strong>{t("attribution.characters.heading")}</strong>
        {stored ? (
          <div className="character-stored">
            <Portrait assetId={stored.portrait_asset_id} workspaceId={workspaceId} apiFetch={apiFetch} />
            <span>
              {stored.archived
                ? t("attribution.characters.removed", { name: stored.name })
                : t("attribution.characters.reviewChosen", { name: stored.name })}
            </span>
            <Button variant="quiet" size="sm" onClick={() => setViewing(stored)}>
              {t("attribution.characters.viewSheet")}
            </Button>
          </div>
        ) : (
          <p>{t("attribution.characters.reviewNone")}</p>
        )}
        {viewer}
      </div>
    );
  }

  const allowed = recipeTakesCharacter(recipe);
  return (
    <div className="generate-use">
      <strong>{t("attribution.characters.heading")}</strong>
      <p>{t("attribution.characters.help")}</p>
      <label className="campaign-dialog-check">
        <input
          type="checkbox"
          checked={enabled}
          disabled={locked || !allowed}
          onChange={(event) => onEnabledChange(event.target.checked)}
        />
        <span>{t("attribution.characters.enable")}</span>
      </label>
      {!allowed && <small>{t("attribution.characters.recipeHasModel")}</small>}
      {enabled && allowed && (
        <>
          {loading ? (
            <p>{t("attribution.characters.loading")}</p>
          ) : characters.length === 0 ? (
            <p>{t("attribution.characters.none")}</p>
          ) : (
            <ul className="character-grid" aria-label={t("attribution.characters.heading")}>
              {characters.map((character) => {
                const pickable = isPickable(character);
                const selected = character.id === characterId;
                return (
                  <li key={character.id} data-selected={selected ? "true" : undefined}>
                    <button
                      type="button"
                      className="character-card"
                      aria-pressed={selected}
                      disabled={locked || !pickable}
                      title={character.appearance}
                      onClick={() => onCharacterChange(selected ? "" : character.id)}
                    >
                      <Portrait assetId={character.portrait_asset_id} workspaceId={workspaceId} apiFetch={apiFetch} />
                      <span className="character-name">{character.name}</span>
                      <small>
                        {character.status === "generating"
                          ? t("attribution.characters.generating")
                          : character.status === "failed"
                            ? t("attribution.characters.failed")
                            : character.builtin ? t("attribution.characters.builtin") : ""}
                      </small>
                    </button>
                    <span className="character-actions">
                      {character.sheet_asset_id && (
                        <Button variant="link" size="sm" onClick={() => setViewing(character)}>
                          {t("attribution.characters.viewSheet")}
                        </Button>
                      )}
                      {character.status === "failed" && !character.builtin && (
                        <Button
                          variant="link"
                          size="sm"
                          busy={busyId === character.id}
                          disabled={busyId !== "" || !generator?.ready}
                          title={character.error ?? undefined}
                          onClick={() => void retry(character)}
                        >{t("attribution.characters.retry")}</Button>
                      )}
                      {!character.builtin && character.status !== "generating" && !locked && (
                        <Button
                          variant="link"
                          size="sm"
                          disabled={busyId !== ""}
                          onClick={() => void remove(character)}
                        >{t("attribution.characters.remove")}</Button>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          {chosen && <small>{t("attribution.characters.chosen", { name: chosen.name })}</small>}
          {!locked && (
            <div className="generate-subject-actions">
              <Button variant="secondary" size="sm" onClick={() => setCreating(true)}>
                {t("attribution.characters.newCharacter")}
              </Button>
              {generator && !generator.ready && <small>{generator.message}</small>}
            </div>
          )}
          {error && <p role="alert">{error}</p>}
        </>
      )}
      {viewer}
      <NewCharacterDialog
        open={creating}
        workspaceId={workspaceId}
        apiFetch={apiFetch}
        generator={generator}
        onClose={() => setCreating(false)}
        onCreated={(character) => {
          setCharacters((current) => [...current, character]);
          void load();
        }}
        onPickerChange={setPickerOpen}
      />
    </div>
  );
}
