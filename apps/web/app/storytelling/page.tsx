"use client";

/**
 * Storytelling: a script and a voice become a narrated video.
 *
 * Its own page rather than a Library dialog, and the reason is structural.
 * Everything in the Library acts on something selected there - an effect, a
 * caption, a montage of chosen pictures. This starts from a script, and a
 * script is not a thing that can be selected in a library, so there is nowhere
 * in there for it to be reached from.
 *
 * The order of the page is the order of the work: write it, hear how it splits,
 * find pictures for it, choose who reads it, watch it. Nothing below is enabled
 * before the step above it can be answered, so the page never offers a button
 * that would fail.
 */

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "../auth-provider";
import { useWorkspace } from "../workspace-provider";
import { useT } from "../i18n-provider";
import { Button } from "../ui/button";
import { Select } from "../ui/select";
import { SegmentedControl } from "../ui/segmented";
import { ActionIcon } from "../ui/action-icons";
import { Badge } from "../ui/primitives";
import { WaitingBlock } from "../ui/waiting-block";
import { useLibraryAssets } from "../../lib/use-library-assets";
import { StatusToasts, useStatus } from "../ui/status";

type Template = { id: string; name: string; description: string };
/** As the voices endpoint reports them. `voice_id`, not `id`: the field is
    ElevenLabs' own and this reads it rather than renaming it in passing. */
type Voice = { voice_id: string; name: string; description?: string | null };
type Picture = { id: string; title: string; media_kind: string };
type Tile = {
  id: string;
  kind: string;
  preview_url: string;
  width: number;
  height: number;
  credit: string;
  photographer: string;
  photographer_url: string;
  page_url: string;
  duration_seconds?: number | null;
};

/** How the finished video is shaped. Landscape leads because a narrated piece
    is watched on a wide screen; the short-form frame is offered beside it. */
const ASPECTS = [
  { value: "16:9", label: "Wide" },
  { value: "9:16", label: "Tall" },
  { value: "1:1", label: "Square" },
] as const;

function StorytellingPage() {
  const t = useT();
  const { loading, user, apiFetch } = useAuth();
  const { workspaceId } = useWorkspace();
  const { messages, succeed, fail, dismiss } = useStatus();

  const [body, setBody] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [outlining, setOutlining] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("explainer");
  const [aspect, setAspect] = useState<string>("16:9");
  const [subtitles, setSubtitles] = useState(true);

  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState("");
  const [chosen, setChosen] = useState<Picture[]>([]);

  const [brollReady, setBrollReady] = useState<boolean | null>(null);
  const [brollReason, setBrollReason] = useState("");
  const [query, setQuery] = useState("");
  const [brollKind, setBrollKind] = useState("image");
  const [tiles, setTiles] = useState<Tile[]>([]);
  const [searching, setSearching] = useState(false);

  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<{ status?: string; error?: string; shots?: number; asset_id?: string } | null>(null);

  const base = workspaceId ? `/api/workspaces/${workspaceId}/storytelling` : "";

  const library = useLibraryAssets<Picture>({
    workspaceId,
    apiFetch,
    // A narration plays over pictures and clips; its own voice is not one of
    // them, so audio is dropped on arrival rather than offered and refused.
    keep: (asset) => asset.media_kind !== "audio",
    enabled: Boolean(workspaceId),
  });

  useEffect(() => {
    if (!base) return;
    let cancelled = false;
    void (async () => {
      const [templateBody, statusBody] = await Promise.all([
        apiFetch(`${base}/templates`).then((r) => r.json()).catch(() => ({ templates: [] })),
        apiFetch(`${base}/broll/status`).then((r) => r.json()).catch(() => null),
      ]);
      if (cancelled) return;
      setTemplates(templateBody.templates ?? []);
      setBrollReady(statusBody ? Boolean(statusBody.configured) : false);
      setBrollReason(statusBody?.reason ?? "");
    })();
    return () => { cancelled = true; };
  }, [apiFetch, base]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    void apiFetch(`/api/workspaces/${workspaceId}/media/library/voice/voices`)
      .then((response) => response.json())
      .then((payload) => {
        if (cancelled) return;
        const found: Voice[] = payload?.voices ?? [];
        setVoices(found);
        setVoiceId((current) => current || found[0]?.voice_id || "");
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  /**
   * How the script splits, read back from the server that will split it.
   *
   * Not computed here. The sentence rules are the renderer's, and a count this
   * page worked out for itself would be a second opinion that quietly differs -
   * on a decomposed accent, on a full-width stop, on an ellipsis.
   */
  const outline = useCallback(async () => {
    if (!base || !body.trim()) { setLines([]); return; }
    setOutlining(true);
    try {
      const response = await apiFetch(`${base}/outline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body }),
      });
      const payload = await response.json();
      setLines(response.ok ? payload.lines ?? [] : []);
    } catch {
      setLines([]);
    } finally {
      setOutlining(false);
    }
  }, [apiFetch, base, body]);

  useEffect(() => {
    const timer = window.setTimeout(() => void outline(), 400);
    return () => window.clearTimeout(timer);
  }, [outline]);

  async function searchBroll() {
    if (!base || !query.trim()) return;
    setSearching(true);
    try {
      const params = new URLSearchParams({
        q: query,
        kind: brollKind,
        // Ask for the shape the video is, so a candidate does not arrive to be
        // cropped in half.
        orientation: aspect === "9:16" ? "portrait" : aspect === "1:1" ? "square" : "landscape",
      });
      const response = await apiFetch(`${base}/broll/search?${params}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That search did not answer.");
      setTiles(payload.results ?? []);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "That search did not answer.");
    } finally {
      setSearching(false);
    }
  }

  async function importTile(tile: Tile) {
    try {
      const response = await apiFetch(`${base}/broll/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // The id and nothing else. Where the file lives is resolved on the
        // server, so this request carries no url to be pointed elsewhere.
        body: JSON.stringify({ id: tile.id, kind: tile.kind, query }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That clip could not be brought in.");
      succeed(`${tile.credit}. It will appear in your pictures once it is filed.`);
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "That clip could not be brought in.");
    }
  }

  function toggle(asset: Picture) {
    setChosen((current) => current.some((item) => item.id === asset.id)
      ? current.filter((item) => item.id !== asset.id)
      : [...current, asset]);
  }

  const ready = Boolean(body.trim() && chosen.length && voiceId && lines.length);

  async function render() {
    try {
      const response = await apiFetch(`${base}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          body,
          asset_ids: chosen.map((item) => item.id),
          template_id: templateId,
          voice_id: voiceId,
          aspect,
          subtitles,
        }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "That could not be queued.");
      setJobId(payload.id);
      setJob({ status: "queued" });
      succeed("Queued. It reads the script, then cuts the pictures to it.");
    } catch (reason) {
      fail(reason instanceof Error ? reason.message : "That could not be queued.");
    }
  }

  useEffect(() => {
    if (!jobId || !base) return;
    if (job?.status === "succeeded" || job?.status === "failed") return;
    const timer = window.setTimeout(() => {
      void apiFetch(`${base}/jobs/${jobId}`)
        .then((response) => response.json())
        .then(setJob)
        .catch(() => undefined);
    }, 2500);
    return () => window.clearTimeout(timer);
  }, [apiFetch, base, jobId, job]);

  const shortOfPictures = lines.length > 0 && chosen.length > 0 && chosen.length < lines.length;

  if (loading || !user) return <WaitingBlock message={t("common.loading")} />;

  return <main className="page storytelling-page">
    <header className="page-heading">
      <div>
        <h1>Storytelling</h1>
        <p>
          A script and a voice, cut to pictures. The cuts land where the
          narrator stops, and the words become the subtitles.
        </p>
      </div>
    </header>

    <section className="story-step">
      <h2><span className="story-step-number">1</span> Write the script</h2>
      <textarea
        className="story-script"
        value={body}
        onChange={(event) => setBody(event.target.value)}
        rows={10}
        placeholder="Paste or write what the narrator says. One sentence becomes one shot; a blank line forces a beat."
      />
      <p className="story-outline" aria-live="polite">
        {outlining ? "Reading…"
          : lines.length
            ? <>
              <strong>{lines.length}</strong> {lines.length === 1 ? "sentence" : "sentences"},
              so it wants about <strong>{lines.length}</strong>{" "}
              {lines.length === 1 ? "picture" : "pictures"}.
            </>
            : "Nothing to read yet."}
      </p>
    </section>

    <section className="story-step">
      <h2><span className="story-step-number">2</span> Choose the pacing</h2>
      <div className="story-templates">
        {templates.map((item) => (
          <button
            key={item.id}
            type="button"
            className={templateId === item.id ? "selected" : ""}
            aria-pressed={templateId === item.id}
            onClick={() => setTemplateId(item.id)}
          >
            <strong>{item.name}</strong>
            <small>{item.description}</small>
          </button>
        ))}
      </div>
      <div className="story-shape">
        <label>Shape
          <SegmentedControl
            value={aspect}
            onChange={setAspect}
            options={ASPECTS}
            label="The shape of the finished video"
          />
        </label>
        <label className="story-toggle">
          <input
            type="checkbox"
            checked={subtitles}
            onChange={(event) => setSubtitles(event.target.checked)}
          />
          <span>Burn in subtitles</span>
          {/* The words are already known, so this costs nothing to be on. */}
          <small>The script&apos;s own words, so nothing is transcribed back.</small>
        </label>
      </div>
    </section>

    <section className="story-step">
      <h2><span className="story-step-number">3</span> Choose the pictures</h2>
      {shortOfPictures && (
        <p className="story-note">
          {chosen.length} chosen for {lines.length} sentences, so some will be
          shown more than once.
        </p>
      )}
      <div className="story-picker">
        <div className="story-picker-library">
          <h3>From your Library</h3>
          <ul className="story-tiles">
            {library.assets.map((asset) => (
              <li key={asset.id}>
                <button
                  type="button"
                  className={chosen.some((item) => item.id === asset.id) ? "selected" : ""}
                  aria-pressed={chosen.some((item) => item.id === asset.id)}
                  onClick={() => toggle(asset)}
                  title={asset.title}
                >
                  <span className="story-tile-name">{asset.title}</span>
                  {asset.media_kind === "video" && <Badge tone="neutral">clip</Badge>}
                </button>
              </li>
            ))}
          </ul>
          {library.canLoadMore && (
            <Button variant="quiet" size="sm" busy={library.loading === "more"}
              onClick={() => void library.loadMore()}>Load more</Button>
          )}
        </div>

        <div className="story-picker-broll">
          <h3>Stock b-roll</h3>
          {brollReady === false ? (
            <p className="story-note">{brollReason}</p>
          ) : (
            <>
              <form className="story-search" onSubmit={(event) => {
                event.preventDefault(); void searchBroll();
              }}>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="What should be on screen?"
                  aria-label="Search stock b-roll"
                />
                <Select
                  value={brollKind}
                  onChange={(event) => setBrollKind(event.target.value)}
                  aria-label="Photos or clips"
                >
                  <option value="image">Photos</option>
                  <option value="video">Clips</option>
                </Select>
                <Button variant="secondary" size="sm" busy={searching} type="submit">
                  <ActionIcon name="search" />Search
                </Button>
              </form>
              <ul className="story-tiles story-tiles-broll">
                {tiles.map((tile) => (
                  <li key={`${tile.kind}-${tile.id}`}>
                    <button type="button" onClick={() => void importTile(tile)}
                      title={`${tile.credit} - bring into the Library`}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={tile.preview_url} alt="" loading="lazy" referrerPolicy="no-referrer" />
                      {/* The credit is on the tile because the licence asks
                          for it wherever the media is shown, and this is one
                          of the places it is shown. */}
                      <small>{tile.credit}</small>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
      <p className="story-chosen" aria-live="polite">
        {chosen.length
          ? `${chosen.length} chosen, in this order.`
          : "Nothing chosen yet."}
      </p>
    </section>

    <section className="story-step">
      <h2><span className="story-step-number">4</span> Choose the voice</h2>
      <Select
        value={voiceId}
        onChange={(event) => setVoiceId(event.target.value)}
        aria-label="The voice that reads the script"
      >
        {voices.map((voice) => (
          <option key={voice.voice_id} value={voice.voice_id}>{voice.name}</option>
        ))}
      </Select>
      <p className="story-note">
        Generated with its own timings, so the cuts land on the sentences
        rather than near them.
      </p>
    </section>

    <section className="story-step story-make">
      <Button variant="primary" disabled={!ready} onClick={() => void render()}>
        <ActionIcon name="play" />Make the video
      </Button>
      {!ready && (
        <span className="story-note">
          {!body.trim() ? "Write the script first."
            : !lines.length ? "Nothing in the script to read."
              : !chosen.length ? "Choose at least one picture."
                : "Choose a voice."}
        </span>
      )}
      {job && (
        <span className="story-job" aria-live="polite">
          {job.status === "succeeded"
            ? <>Done - {job.shots} shots. It is in your Library.</>
            : job.status === "failed"
              ? <>Stopped: {job.error}</>
              : <>Working…</>}
        </span>
      )}
    </section>
    <StatusToasts messages={messages} onDismiss={dismiss} />
  </main>;
}

export default function Page() {
  return <Suspense fallback={null}><StorytellingPage /></Suspense>;
}
