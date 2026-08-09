"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiBaseUrl } from "../../lib/api";
import { useAuth } from "../auth-provider";
import { useT } from "../i18n-provider";
import { useJobs } from "../jobs-provider";
import {
  PlatformIcon,
  ProviderMark,
  platformLabels,
  type PublishingPlatform,
  type PublishingProvider,
} from "../publishing-icons";
import { WorkspaceSectionNav } from "../workspace-section-nav";
import { oneOf, usePersistedState } from "../ui/use-persisted-state";
import {
  EMPTY_FACETS,
  assetFilterParams,
  type AssetFacets,
  type AssetFilterValues,
} from "../ui/asset-filters";
import { ActionIcon } from "../ui/action-icons";
import { Button, buttonClass } from "../ui/button";
import { Badge, Switch } from "../ui/primitives";
import {
  MEDIA_DRAG_TYPE,
  MediaPicker,
  PICKER_BASE,
  PostPreview,
  SlotEditor,
  UpcomingPosts,
  WeekCalendar,
  clipLength,
  isBlurred,
  localValue,
  upcomingSlots,
  type CalendarEntry,
  type LibraryAsset,
  type Slot,
  type SlotPreset,
} from "./composer";

type Delivery = "draft" | "schedule" | "now";
const isDelivery = oneOf<Delivery>("draft", "schedule", "now");

type Workspace = { id: string; name: string; role: string };
type Allowance = {
  id: string;
  label: string;
  /** How much the figure can be trusted, which is as important as the figure. */
  confidence: "measured" | "counted" | "published";
  limit: number | null;
  used: number | null;
  remaining: number | null;
  unlimited: boolean;
  note: string;
};

type SocialPage = {
  key: string;
  platform: PublishingPlatform;
  handle: string | null;
  label: string;
  shared: boolean;
  engine_count: number;
  reachable_by: Array<{
    provider: PublishingProvider;
    provider_label: string;
    id: string;
    label: string;
  }>;
  default_provider: PublishingProvider;
  default_integration_id: string;
};

type Account = {
  id: string;
  label: string;
  platform: PublishingPlatform;
  /** The engine that reaches this account, and so will deliver to it. */
  provider: PublishingProvider;
  provider_label: string;
};
type EngineReach = {
  allowances?: Allowance[];
  id: string; label: string; reachable: boolean; reason: string | null; account_count: number;
};
type CredentialField = {
  id: string;
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string;
  configured: boolean;
};
type PostTypeOption = { id: string; label: string; help: string };
type PlatformLimit = { caption: number; title: number | null };
type Provider = {
  post_types: Record<string, PostTypeOption[]>;
  limits: Record<string, PlatformLimit>;
  first_comment_platforms: string[];
  thread_platforms: string[];
  max_thread_parts: number;
  supports_approval: boolean;
  id: PublishingProvider;
  label: string;
  tagline: string;
  summary: string;
  homepage: string;
  dashboard_url: string;
  channels_url: string;
  docs_url: string;
  accent: string;
  platforms: PublishingPlatform[];
  requires_public_media: boolean;
  media_note: string;
  configured: boolean;
  authenticated: boolean;
  authorization_error: string | null;
  credential_fields: CredentialField[];
  account_count?: number;
};
type MediaHosting = {
  label: string;
  dashboard_url?: string;
  configured: boolean;
  required: boolean;
  reason: string | null;
  credential_fields: CredentialField[];
};
type Connection = {
  media_hosting: MediaHosting;
  active_provider: PublishingProvider;
  configured: boolean;
  authenticated: boolean;
  service_ready: boolean;
  authorization_error: string | null;
  next_step: string;
  supported_platforms: PublishingPlatform[];
  providers: Provider[];
};
type Destination = {
  platform: PublishingPlatform;
  label: string;
  post_type_label: string;
  notes: string[];
};
type Preview = {
  provider_label: string;
  delivery: string;
  date: string;
  media_source: string;
  media_handling: string;
  caption: string;
  title: string | null;
  visibility: string;
  made_with_ai: boolean;
  destinations: Destination[];
};

async function json<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail ?? "Publishing request failed.");
  return body;
}

function localDateTime(offsetMinutes: number) {
  const value = new Date(Date.now() + offsetMinutes * 60_000);
  value.setSeconds(0, 0);
  return localValue(value);
}

/** Where an unsent post is kept between reloads. */
function sinceLabel(iso: string) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

const DRAFT_KEY = "trendrelay.publish.draft";

export default function PublishPage() {
  const t = useT();
  const { loading, user, apiFetch } = useAuth();
  const { jobs: allJobs, setActiveWorkspaceId, refresh: refreshJobs } = useJobs();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [videoPath, setVideoPath] = useState("");
  const [mediaUrl, setMediaUrl] = useState("");
  // Accounts from every engine at once, each carrying its own, so one post can
  // reach a TikTok on one engine and a YouTube on another.
  const [allAccounts, setAllAccounts] = useState<Account[]>([]);
  /**
   * Accounts grouped by the page they actually are.
   *
   * Two engines on one brand report its Instagram twice under two ids. Picking
   * both would publish the same post to the same audience twice, so the picker
   * offers the page and the post goes through one engine.
   */
  const [pages, setPages] = useState<SocialPage[]>([]);
  /** Where the operator has overridden which engine delivers a shared page. */
  const [routeFor, setRouteFor] = useState<Record<string, string>>({});
  const [engineReach, setEngineReach] = useState<EngineReach[]>([]);
  const [accountsLoaded, setAccountsLoaded] = useState(false);
  const [connection, setConnection] = useState<Connection | null>(null);
  /**
   * Chosen destinations, as account ids in the order they were picked.
   *
   * Was one account per platform. That shape could not express "both TikTok
   * accounts", which is the case running two engines is for, and it silently
   * dropped the second choice rather than refusing it.
   */
  const [targets, setTargets] = useState<string[]>([]);
  const [credentialDrafts, setCredentialDrafts] = useState<Record<string, Record<string, string>>>({});
  const [openProvider, setOpenProvider] = useState<string | null>(null);
  /** Post type per destination, keyed by account: two accounts on one network
      can go out as a Reel and as a Story. */
  const [postTypes, setPostTypes] = useState<Record<string, string>>({});
  /**
   * Engines switched off for publishing, by id.
   *
   * A key that works is not the same as an engine you want this post to use. A
   * workspace can hold a client's engine alongside its own, and without a
   * switch the only way to keep a post off one was to remember not to pick its
   * accounts - which is a rule you break once and discover afterwards.
   *
   * Stored here rather than on the server: it is a preference about composing,
   * not a policy about the workspace, and every destination still names the
   * engine that will deliver it.
   */
  const [disabledEngines, setDisabledEngines] = usePersistedState<string[]>(
    "trendrelay.publish.disabledEngines",
    [],
    (value): value is string[] =>
      Array.isArray(value) && value.every((item) => typeof item === "string"),
  );
  const [hostingDraft, setHostingDraft] = useState<Record<string, string>>({});
  const [hostingOpen, setHostingOpen] = useState(false);
  const draftRestored = useRef(false);
  // Publishing now is what most posts are for, and the choice is a habit
  // rather than a per-post decision, so it is remembered between sessions.
  const [delivery, setDelivery] = usePersistedState<Delivery>(
    "trendrelay.publish.delivery", "now", isDelivery,
  );
  const [date, setDate] = useState(() => localDateTime(60));
  const [caption, setCaption] = useState("");
  const [firstComment, setFirstComment] = useState("");
  /** Replies after the caption, which is itself the first post of the thread. */
  const [thread, setThread] = useState<string[]>([]);
  const [needsApproval, setNeedsApproval] = useState(false);
  const [title, setTitle] = useState("");
  // The clock is read when the schedule pane opens, so a slot never drifts past.
  const [now, setNow] = useState(() => new Date());
  const [slots, setSlots] = useState<Slot[]>([]);
  const [slotPresets, setSlotPresets] = useState<SlotPreset[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  /** Engine setup is configured once and then in the way; it folds down to a
      line as soon as the active engine can actually publish. */
  const [setupOpen, setSetupOpen] = useState(false);
  const [clip, setClip] = useState<LibraryAsset | null>(null);
  const [thumbnail, setThumbnail] = useState("");
  const [library, setLibrary] = useState<LibraryAsset[]>([]);
  const [libraryFacets, setLibraryFacets] = useState<AssetFacets>(EMPTY_FACETS);
  const [libraryState, setLibraryState] = useState<{ loading: boolean; failure: string | null }>({
    loading: false,
    failure: null,
  });

  function chooseDelivery(mode: Delivery) {
    if (mode === "schedule") setNow(new Date());
    setDelivery(mode);
  }
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = workspaces.find((workspace) => workspace.id === workspaceId);
  const canExecute = selected?.role === "owner" || selected?.role === "approver";
  const jobs = allJobs.filter((job) => job.category === "publish").map((job) => job.raw);
  const activeProvider = connection?.providers.find((item) => item.id === connection.active_provider) ?? null;
  const providerById = useMemo(
    () => new Map((connection?.providers ?? []).map((item) => [item.id, item])),
    [connection],
  );
  const engineOff = (id: string) => disabledEngines.includes(id);
  /**
   * What is true of one engine right now, in one place.
   *
   * Three sources used to be read separately and phrased differently: whether a
   * key is saved, whether the engine accepted it, and whether the account list
   * could actually be read. A key can be saved and rejected, accepted and
   * return nothing, or work yesterday and time out today, and each of those
   * needs a different thing done about it.
   *
   * Condition only. Whether the operator wants to use the engine is the switch,
   * and mixing the two here made a healthy switched-off engine report "off"
   * while a broken switched-off one reported "key refused" - one decision,
   * described two ways depending on something unrelated to it.
   */
  const engineState = (provider: Provider): {
    state: "ready" | "no-key" | "rejected" | "unreachable" | "no-accounts";
    tone: "good" | "warn" | "bad" | "neutral";
    detail: string;
    fix: string | null;
  } => {
    const reach = engineReach.find((item) => item.id === provider.id) ?? null;
    const count = reach?.account_count ?? provider.account_count ?? 0;
    if (!provider.configured) {
      return {
        state: "no-key",
        tone: "neutral",
        detail: t("publish.engineNoKey"),
        fix: t("publish.engineNoKeyFix"),
      };
    }
    // The engine's own words first: "invalid API key" is more use than
    // anything this page could infer from a false flag.
    const refusal = reach && !reach.reachable
      ? reach.reason ?? provider.authorization_error
      : provider.authorization_error;
    // The account load wins over the credential probe, because it is the more
    // recent and more direct evidence: it asked this engine for these accounts
    // and got them. Reading both as equal listed Buffer as unreachable directly
    // above the three Buffer accounts it had just returned.
    const working = reach ? reach.reachable : provider.authenticated;
    if (!working) {
      const rejected = /401|403|key|token|auth|unauthor|forbidden/i.test(refusal ?? "");
      return {
        state: rejected ? "rejected" : "unreachable",
        tone: "bad",
        detail: refusal ?? t(rejected ? "publish.engineRejected" : "publish.engineUnreachable"),
        fix: t(rejected ? "publish.engineRejectedFix" : "publish.engineUnreachableFix"),
      };
    }
    if (accountsLoaded && count === 0) {
      return {
        state: "no-accounts",
        tone: "warn",
        detail: t("publish.engineNoAccounts"),
        fix: t("publish.engineNoAccountsFix"),
      };
    }
    return {
      state: "ready",
      tone: "good",
      detail: t("publish.engineReadyDetail", { count }),
      fix: null,
    };
  };

  /**
   * The destinations actually offered.
   *
   * An engine switched off keeps its accounts out of the picker entirely. A
   * greyed row you cannot choose is the same information with more to read.
   */
  const accounts = useMemo(
    () => allAccounts.filter((account) => !disabledEngines.includes(account.provider)),
    [allAccounts, disabledEngines],
  );

  /** The engine that will deliver a destination, read from the account itself. */
  const engineFor = (accountId: string) =>
    accounts.find((item) => item.id === accountId)?.provider ?? null;
  /** The engine definition behind one chosen account. */
  const providerOf = (accountId: string): Provider | null => {
    const engine = engineFor(accountId);
    return (engine ? providerById.get(engine) : null) ?? activeProvider;
  };
  /**
   * Every engine delivering one network on this post.
   *
   * Each capability question here - can it thread, does it take a first
   * comment, how long may the caption be, does it fetch the media - is a
   * question about the engine behind *that destination*, and a network can now
   * appear twice under two engines. Asking one active engine on behalf of all
   * of them was right only while a post could use one.
   */
  const providersFor = (platform: string): Provider[] => {
    const ids = [...new Set(
      chosenAccounts.filter((item) => item.platform === platform)
        .map((item) => item.provider),
    )];
    const found = ids.map((id) => providerById.get(id)).filter((item): item is Provider =>
      Boolean(item));
    return found.length ? found : activeProvider ? [activeProvider] : [];
  };
  // Every network any engine can reach, rather than one engine's list.
  const platforms = useMemo(
    () => [...new Set(accounts.map((account) => account.platform))],
    [accounts],
  );
  const connectedPlatforms = platforms;
  /** The accounts this post goes to, in the order they were chosen. */
  const chosenAccounts = useMemo(
    () => targets
      .map((id) => accounts.find((account) => account.id === id))
      .filter((account): account is Account => Boolean(account)),
    [accounts, targets],
  );
  /** The networks reached, each named once however many accounts are on it. */
  const chosen = useMemo(
    () => [...new Set(chosenAccounts.map((account) => account.platform))],
    [chosenAccounts],
  );
  /** The engines actually delivering this post, in the order chosen. */
  const chosenEngines = useMemo(() => {
    const seen: PublishingProvider[] = [];
    for (const account of chosenAccounts) {
      if (!seen.includes(account.provider)) seen.push(account.provider);
    }
    return seen;
  }, [chosenAccounts]);
  const chosenProviders = chosenEngines
    .map((id) => providerById.get(id))
    .filter((item): item is Provider => Boolean(item));
  // Any engine, not the active one. If one destination is delivered by an
  // engine that fetches media, a URL is needed even when the others accept an
  // upload - and reading this from whichever engine happened to be active let a
  // post reach submission with nothing for that engine to fetch.
  const fetchOnlyProviders = chosenProviders.filter((item) => item.requires_public_media);
  /**
   * Which engine delivers a page, honouring an override.
   *
   * Falls back to the first route rather than failing, so a stale override -
   * an engine that has since been switched off, say - degrades to posting
   * through a working one instead of silently dropping the destination.
   */
  const routeOf = (page: SocialPage) => {
    const chosen = routeFor[page.key];
    return page.reachable_by.find((item) => `${item.provider}:${item.id}` === chosen)
      ?? page.reachable_by[0];
  };
  /** A page is chosen when any of its routes is in the target list. */
  const pageChosen = (page: SocialPage) =>
    page.reachable_by.some((item) => targets.includes(item.id));
  /**
   * Select or clear a page.
   *
   * Every route is cleared before one is added, so a page can never contribute
   * two targets. That is the duplicate this grouping exists to prevent, and it
   * would be caused by the grouping itself.
   */
  const togglePage = (page: SocialPage, on: boolean) => setTargets((current) => {
    const without = current.filter(
      (id) => !page.reachable_by.some((item) => item.id === id));
    return on ? [...without, routeOf(page).id] : without;
  });
  const switchRoute = (page: SocialPage, provider: string, id: string) => {
    setRouteFor((current) => ({ ...current, [page.key]: `${provider}:${id}` }));
    setTargets((current) => {
      if (!page.reachable_by.some((item) => current.includes(item.id))) return current;
      return [
        ...current.filter((existing) => !page.reachable_by.some((item) => item.id === existing)),
        id,
      ];
    });
  };
  /** Pages whose engine is switched off here are not offered at all. */
  const offeredPages = pages
    .map((page) => ({
      ...page,
      reachable_by: page.reachable_by.filter((item) => !engineOff(item.provider)),
    }))
    .filter((page) => page.reachable_by.length > 0);

  /** "Buffer" or "Buffer and Zernio" - what this post actually goes out through. */
  const engineNames = (list: Provider[]) =>
    list.length > 1
      ? `${list.slice(0, -1).map((item) => item.label).join(", ")} and ${list[list.length - 1].label}`
      : list[0]?.label ?? "";
  const deliveringNames = engineNames(chosenProviders);
  const fetchingNames = engineNames(fetchOnlyProviders);
  /** Engines on this post that can hold a draft for a teammate to approve. */
  const approvers = chosenProviders.filter((item) => item.supports_approval);
  /** Engines that work and are switched on - these are the ones carrying posts. */
  const connectedEngines = (connection?.providers ?? []).filter(
    (item) => engineState(item).state === "ready" && !engineOff(item.id));
  /**
   * Engines whose key works, switched on or not.
   *
   * Kept apart from the list above so that turning every engine off does not
   * report the same thing as never having set one up. One is a choice you just
   * made and can undo; the other is work you have not done yet.
   */
  const usableEngines = (connection?.providers ?? []).filter(
    (item) => ["ready", "no-accounts"].includes(engineState(item).state));
  /**
   * Engines whose key works and that are switched on, channels or not.
   *
   * "Nothing is switched on" and "what is switched on has no channels yet" are
   * different problems with different fixes, and reporting the second as the
   * first sent you to a switch that was already on.
   */
  const switchedOnEngines = usableEngines.filter((item) => !engineOff(item.id));
  /**
   * Engines that have been set up but cannot deliver right now.
   *
   * Surfaced rather than omitted. An engine whose key was revoked simply stops
   * contributing accounts, so the picker quietly gets shorter - which looks
   * exactly like those channels having been disconnected at the network, and
   * sends you to the wrong dashboard to fix it.
   */
  const unavailableEngines = (connection?.providers ?? [])
    .map((item) => ({ provider: item, status: engineState(item) }))
    // Switched off is a decision, not a fault. An engine the operator turned
    // off has no business in a list of things that need attention - it is
    // already reported on the line below as switched off, and naming it here
    // too asks them to fix something they chose.
    .filter(({ provider, status }) =>
      !engineOff(provider.id)
      && ["rejected", "unreachable", "no-accounts"].includes(status.state));
  /** Chosen destinations whose engine has stopped working since they were picked. */
  const brokenChoices = chosenProviders.filter(
    (item) => engineState(item).state !== "ready");
  /**
   * How the media reaches the engines, said once.
   *
   * Two engines with different notes both apply, so both are shown; identical
   * notes are said once rather than twice, which is what happens when three
   * destinations share an engine.
   */
  const mediaNote = [...new Set(
    (chosenProviders.length ? chosenProviders : activeProvider ? [activeProvider] : [])
      .map((item) => item.media_note)
      .filter(Boolean),
  )].join(" ") || null;
  // Only once a destination has been chosen. Falling back to the default
  // engine meant warning that Buffer needs a publicly hosted file before the
  // operator had picked anything - a constraint from a choice they had not
  // made, presented as a problem with the clip.
  const needsPublicMedia = chosenProviders.length > 0 && fetchOnlyProviders.length > 0;
  const hosting = connection?.media_hosting ?? null;
  // With storage configured the engine still fetches, but TrendRelay does the
  // hosting, so a local path is enough and no URL has to be found by hand.
  const hostsLocalMedia = needsPublicMedia && (hosting?.configured ?? false);
  const checking = !connection && !error;
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "your local time";
  const quickSlots = useMemo(() => upcomingSlots(slots, now), [slots, now]);
  // Only work that is still going to happen belongs on a calendar. A draft has
  // no time to keep, and a failed post is history rather than a commitment -
  // showing either would make the week look busier than it is.
  const scheduled = useMemo<CalendarEntry[]>(
    () =>
      jobs
        .filter((job) => {
          const request = job.payload?.request;
          if (!request?.date || job.status === "failed") return false;
          return request.delivery === "schedule"
            || (!request.delivery && request.schedule === true);
        })
        .map((job) => ({
          at: new Date(job.payload.request.date),
          label: job.payload.request.caption ?? "Scheduled post",
          state: job.status,
          title: job.payload.request.title ?? null,
          platforms: (job.payload.request.targets ?? [])
            .map((target: { platform: PublishingPlatform }) => target.platform)
            .filter(Boolean),
        }))
        .filter((entry) => !Number.isNaN(entry.at.getTime())),
    [jobs],
  );
  const scheduledTimes = useMemo(
    () => scheduled.map((entry) => localValue(entry.at)),
    [scheduled],
  );
  const nextFree = useMemo(() => {
    const taken = new Set(scheduledTimes);
    return quickSlots.find((slot) => !taken.has(slot.value)) ?? null;
  }, [quickSlots, scheduledTimes]);

  // The preview stands in for the first destination, which is the one being composed.
  const previewAccount = chosenAccounts[0] ?? null;
  const previewPlatform = previewAccount?.platform ?? null;
  /** Post types this account can take, from the engine that will deliver it. */
  const postTypesFor = (accountId: string) => {
    const account = accounts.find((item) => item.id === accountId);
    if (!account) return [];
    return providerById.get(account.provider)?.post_types?.[account.platform] ?? [];
  };
  const previewType = previewAccount
    ? postTypesFor(previewAccount.id).find(
        (kind) => kind.id === (postTypes[previewAccount.id]
          ?? postTypesFor(previewAccount.id)[0]?.id),
      )
    : null;
  /** One caption goes to every destination, so the shortest limit is the real
      one - and knowing which network sets it is what lets you decide whether to
      trim or to drop that destination.
   *
   *  Each limit comes from the engine delivering that destination. Two engines
   *  publishing to the same network do not always allow the same length, and
   *  taking every limit from one active engine quietly reported a ceiling that
   *  did not apply to half the post. */
  const chosenLimits: Array<{
    platform: PublishingPlatform; caption: number; title: number | null;
  }> = [];
  for (const platform of chosen) {
    // The strictest engine delivering this network. Two engines publishing to
    // the same place need not allow the same length, and the caption is one
    // text sent to all of them.
    const caps = providersFor(platform)
      .map((provider) => provider.limits?.[platform])
      .filter((item): item is PlatformLimit => Boolean(item));
    if (!caps.length) continue;
    const captions = caps.map((item) => item.caption);
    const titles = caps.map((item) => item.title)
      .filter((value): value is number => typeof value === "number");
    chosenLimits.push({
      platform,
      caption: Math.min(...captions),
      title: titles.length ? Math.min(...titles) : null,
    });
  }
  const captionLimit = chosenLimits.length
    ? chosenLimits.reduce((tightest, entry) => (tightest.caption <= entry.caption ? tightest : entry))
    : null;
  const titledLimits = chosenLimits.filter(
    (entry): entry is typeof entry & { title: number } => entry.title !== null);
  const titleLimit = titledLimits.length
    ? titledLimits.reduce((tightest, entry) => (tightest.title <= entry.title ? tightest : entry))
    : null;
  const captionOver = captionLimit ? caption.length - captionLimit.caption : 0;
  const titleOver = titleLimit ? title.length - titleLimit.title : 0;

  const scheduledAt = delivery === "schedule" && date ? new Date(date) : null;
  // Compared against the clock read when the schedule pane opened, since
  // reading it during render would make the same props draw differently.
  const scheduledInPast = Boolean(scheduledAt && scheduledAt.getTime() <= now.getTime());

  /** Why the submit is unavailable, so it is never dead without explanation. */
  const blockedReason = !canExecute
    ? "Only owners and approvers can publish"
    // Named before submitting, because the alternative is that engine refusing
    // the post after the others have already published, and a live post cannot
    // be taken back.
    : brokenChoices.length
      ? t("publish.blockedEngine", { engines: engineNames(brokenChoices) })
      : !chosen.length
      ? "Choose at least one destination"
      : !caption.trim()
        ? "Write a caption"
        : captionOver > 0
          ? `Caption is ${captionOver} over the ${platformLabels[captionLimit!.platform as PublishingPlatform]} limit`
          : titleOver > 0
            ? `Title is ${titleOver} over the ${platformLabels[titleLimit!.platform as PublishingPlatform]} limit`
            : scheduledInPast
              ? "Pick a time in the future"
              : null;

  const previewHandle = previewAccount?.label ?? "";

  useEffect(() => {
    queueMicrotask(() => {
      const handoff = new URLSearchParams(window.location.search).get("video");
      if (handoff) setVideoPath(handoff);
      // A caption is the expensive part of a post to retype, and this page is
      // reloaded often - after saving a key, after switching engine. Restore
      // what was being written unless a handoff is bringing its own clip.
      try {
        const saved = JSON.parse(window.localStorage.getItem(DRAFT_KEY) ?? "null");
        if (!saved) return;
        if (typeof saved.caption === "string") setCaption(saved.caption);
        if (typeof saved.title === "string") setTitle(saved.title);
        if (!handoff && typeof saved.videoPath === "string") setVideoPath(saved.videoPath);
        if (typeof saved.mediaUrl === "string") setMediaUrl(saved.mediaUrl);
        if (typeof saved.firstComment === "string") setFirstComment(saved.firstComment);
        if (Array.isArray(saved.thread)) setThread(saved.thread.filter(
          (part: unknown) => typeof part === "string"));
      } catch {
        // A draft that cannot be read is not worth reporting; start clean.
      } finally {
        draftRestored.current = true;
      }
    });
  }, []);

  useEffect(() => {
    // Nothing is written until the restore has run. On mount these fields are
    // empty, and saving that would erase the draft this page exists to bring
    // back - the save would win the race against its own restore.
    if (!draftRestored.current) return;
    const draft = { caption, title, videoPath, mediaUrl, firstComment, thread };
    const empty = !caption && !title && !videoPath && !mediaUrl && !firstComment;
    try {
      if (empty) window.localStorage.removeItem(DRAFT_KEY);
      else window.localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    } catch {
      // Storage can be full or blocked; losing a draft is not worth an error.
    }
  }, [caption, title, videoPath, mediaUrl, firstComment, thread]);

  useEffect(() => {
    setActiveWorkspaceId(workspaceId || null);
  }, [workspaceId, setActiveWorkspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    apiFetch(`/api/workspaces/${workspaceId}/publishing/slots`)
      .then((response) => json<{ slots: Slot[]; presets: SlotPreset[] }>(response))
      .then((body) => {
        if (cancelled) return;
        setSlots(body.slots);
        setSlotPresets(body.presets);
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!user) return;
    apiFetch("/api/workspaces")
      .then((response) => json<{ workspaces: Workspace[] }>(response))
      .then((body) => {
        setWorkspaces(body.workspaces);
        setWorkspaceId(body.workspaces[0]?.id ?? "");
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not load workspaces."));
  }, [apiFetch, user]);

  const loadConnection = useCallback(async () => {
    const body = await json<{ connection: Connection }>(
      await apiFetch(`/api/workspaces/${workspaceId}/publishing/connection`),
    );
    setConnection(body.connection);
  }, [apiFetch, workspaceId]);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;
    apiFetch(`/api/workspaces/${workspaceId}/publishing/connection`)
      .then((response) => json<{ connection: Connection }>(response))
      .then((body) => { if (!cancelled) setConnection(body.connection); })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Could not check publishing setup.");
      });
    return () => { cancelled = true; };
  }, [apiFetch, workspaceId]);

  function requestFrom(form: FormData, confirm: boolean) {
    const selectedTargets = chosenAccounts.map((account) => ({
      platform: account.platform,
      integration_id: account.id,
      post_type: postTypes[account.id] ?? null,
      // Taken from the chosen account rather than from one active engine, which
      // is what lets a single post go out through several at once - and lets
      // two accounts on one network each go through their own.
      provider: account.provider,
    }));
    if (!selectedTargets.length) throw new Error("Choose at least one connected destination.");
    const localDate = String(form.get("date") ?? "");
    if (!localDate) throw new Error("Choose a date and time.");
    const mediaUrl = String(form.get("media_url") ?? "").trim();
    const localPath = String(form.get("video_path") ?? "").trim();
    if (needsPublicMedia && !mediaUrl && !(hostsLocalMedia && localPath)) {
      // Names the engine that actually needs it, which may not be the one that
      // happens to be active - otherwise the message sends you to check the
      // settings of an engine this post never touches.
      const asking = fetchOnlyProviders[0] ?? activeProvider;
      throw new Error(
        hostsLocalMedia
          ? "Enter the approved local MP4 path, or a public media URL."
          : `${asking?.label} needs a public media URL. ${asking?.media_note}`,
      );
    }
    if (!needsPublicMedia && !localPath && !mediaUrl) {
      throw new Error("Enter the approved local MP4 path.");
    }
    return {
      workspace_id: workspaceId,
      // The lead engine, only a fallback for a destination that names none.
      // Every target above names its own, so this decides nothing on its own.
      provider: chosenEngines[0] ?? connection?.active_provider ?? null,
      video_path: localPath || "unused",
      media_url: mediaUrl || null,
      caption: form.get("caption"),
      first_comment: firstComment.trim() || null,
      thread: thread.map((part) => part.trim()).filter(Boolean),
      // Only where an engine on this post can actually hold it. The checkbox
      // hides when no chosen engine supports approval, but the state it left
      // behind would otherwise still travel.
      needs_approval: approvers.length > 0 && needsApproval,
      title: form.get("title") || null,
      date: new Date(localDate).toISOString(),
      delivery,
      schedule: delivery === "schedule",
      made_with_ai: form.get("made_with_ai") === "on",
      visibility: form.get("visibility") === "private" ? "private" : "public",
      subreddit: form.get("subreddit") || null,
      board: form.get("board") || null,
      targets: selectedTargets,
      confirm_external_action: confirm,
    };
  }

  async function saveCredentials(provider: Provider, activate: boolean) {
    const values = credentialDrafts[provider.id] ?? {};
    const missing = provider.credential_fields.filter(
      (field) => field.required && !field.configured && !values[field.id]?.trim(),
    );
    if (missing.length) {
      setError(`Enter the ${provider.label} ${missing.map((field) => field.label).join(" and ")}.`);
      return;
    }
    const payload = Object.fromEntries(
      Object.entries(values).filter(([, value]) => value.trim().length > 0),
    );
    if (!Object.keys(payload).length && !activate) {
      setError(`Nothing new to save for ${provider.label}.`);
      return;
    }
    if (!window.confirm(`Write the ${provider.label} API settings to this machine's .env file?`)) return;
    setBusy(`${provider.id}-credentials`);
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ connection: Connection; result: { written_keys: string[] } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/providers/credentials`, {
          method: "POST",
          body: JSON.stringify({
            provider: provider.id,
            values: payload,
            activate,
            confirm_external_action: true,
          }),
        }),
      );
      setCredentialDrafts((current) => ({ ...current, [provider.id]: {} }));
      setConnection(body.connection);
      if (activate) { setTargets([]); setPostTypes({}); }
      setOpenProvider(null);
      setNotice(
        `Saved ${body.result.written_keys.join(", ")} to .env.` +
        (activate ? ` ${t("publish.nowDefault", { label: provider.label })}` : ""),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Credentials could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  const loadLibrary = useCallback(
    async (filters: AssetFilterValues = PICKER_BASE) => {
      setLibraryState({ loading: true, failure: null });
      try {
        // Built by the shared serialiser, so the picker and the Library page
        // cannot express the same filter as two different requests.
        const params = assetFilterParams(filters);
        params.set("limit", "40");
        const body = await json<{ assets: LibraryAsset[]; facets?: AssetFacets }>(
          await apiFetch(`/api/workspaces/${workspaceId}/media/library/assets?${params}`),
        );
        setLibrary(body.assets ?? []);
        if (body.facets) setLibraryFacets(body.facets);
        setLibraryState({ loading: false, failure: null });
      } catch (reason) {
        setLibraryState({
          loading: false,
          failure: reason instanceof Error ? reason.message : "The library could not be read.",
        });
      }
    },
    [apiFetch, workspaceId],
  );

  /** Accepts a drag from the library picker, and says so when a file is
      dropped instead - a browser gives no filesystem path for one, so it has
      to be imported before it can be published. */
  function dropMedia(event: React.DragEvent) {
    event.preventDefault();
    setDragOver(false);
    const path = event.dataTransfer.getData(MEDIA_DRAG_TYPE)
      || event.dataTransfer.getData("text/plain");
    if (path) {
      setVideoPath(path.trim());
      setClip(null);
      setThumbnail("");
      setNotice("Clip taken from the library.");
      return;
    }
    if (event.dataTransfer.files.length) {
      setError(
        "A file dropped from your computer has no path TrendRelay can read. "
        + "Import it in Library first, then drag it from there.",
      );
    }
  }

  function openPicker() {
    setPickerOpen(true);
    void loadLibrary();
  }

  async function saveSlots(entries: { weekday: number; time: string }[]) {
    setBusy("slots");
    setError(null);
    try {
      const body = await json<{ slots: Slot[]; presets: SlotPreset[] }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/slots`, {
          method: "POST",
          body: JSON.stringify({ slots: entries }),
        }),
      );
      setSlots(body.slots);
      setSlotPresets(body.presets);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Posting times could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  /** Take a clip from the library, and show the frame it will go out with. */
  function pickClip(asset: LibraryAsset) {
    setClip(asset);
    setVideoPath(asset.original_path);
    setPickerOpen(false);
    setThumbnail("");
    if (!asset.versions.some((version) => version.kind === "thumbnail")) return;
    apiFetch(`/api/workspaces/${workspaceId}/media/library/assets/${asset.id}/content/thumbnail`)
      .then((response) => (response.ok ? response.blob() : Promise.reject(new Error("no frame"))))
      .then((blob) => setThumbnail(URL.createObjectURL(blob)))
      .catch(() => undefined);
  }

  async function saveHosting() {
    const fields = hosting?.credential_fields ?? [];
    const missing = fields.filter(
      (field) => field.required && !field.configured && !hostingDraft[field.id]?.trim(),
    );
    if (missing.length) {
      setError(`Enter the ${missing.map((field) => field.label).join(", ")}.`);
      return;
    }
    const payload = Object.fromEntries(
      Object.entries(hostingDraft).filter(([, value]) => value.trim().length > 0),
    );
    if (!Object.keys(payload).length) {
      setError("Nothing new to save for media hosting.");
      return;
    }
    if (!window.confirm("Write the media hosting settings to this machine's .env file?")) return;
    setBusy("hosting-credentials");
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ connection: Connection; result: { written_keys: string[] } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/media-hosting/credentials`, {
          method: "POST",
          body: JSON.stringify({ values: payload, confirm_external_action: true }),
        }),
      );
      setHostingDraft({});
      setConnection(body.connection);
      setHostingOpen(false);
      setNotice(`Saved ${body.result.written_keys.join(", ")} to .env. Local clips can now be published to engines that fetch media.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Media hosting could not be saved.");
    } finally {
      setBusy(null);
    }
  }

  async function testProvider(provider: Provider) {
    setBusy(`${provider.id}-test`);
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ provider: Provider & { account_count: number } }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/providers/test`, {
          method: "POST",
          body: JSON.stringify({ provider: provider.id }),
        }),
      );
      const result = body.provider;
      if (result.authenticated) {
        setNotice(
          `${provider.label} responded. ${result.account_count} connected account${result.account_count === 1 ? "" : "s"} visible to this key.`,
        );
      } else {
        setError(result.authorization_error ?? `${provider.label} did not accept this key.`);
      }
      await loadConnection();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${provider.label} could not be reached.`);
    } finally {
      setBusy(null);
    }
  }

  async function activateProvider(provider: Provider) {
    setBusy(`${provider.id}-activate`);
    setError(null);
    setNotice(null);
    try {
      const body = await json<{ connection: Connection }>(
        await apiFetch(`/api/workspaces/${workspaceId}/publishing/providers/activate`, {
          method: "POST",
          body: JSON.stringify({ provider: provider.id }),
        }),
      );
      setConnection(body.connection);
      setPreview(null);
      setNotice(`${provider.label} is now the default engine for destinations that name none. Every connected engine still delivers its own accounts.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Engine could not be switched.");
    } finally {
      setBusy(null);
    }
  }

  const autoLoaded = useRef(false);

  useEffect(() => {
    if (!workspaceId || !connection?.configured || autoLoaded.current) return;
    autoLoaded.current = true;
    // Deferred so the fetch does not run inside the render that scheduled it.
    queueMicrotask(() => void refreshAccounts({ quiet: true }));
    // Loaded once per page; the ref is the guard, so re-running on the
    // callback's identity would only repeat the same call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, connection?.configured]);

  async function refreshAccounts(options: { quiet?: boolean } = {}) {
    if (!workspaceId) return;
    setBusy("accounts");
    setError(null);
    if (!options.quiet) setNotice(null);
    try {
      // Every engine at once: a post can address destinations on more than one,
      // so offering only the active engine's accounts would hide the rest.
      const result = await json<{
        accounts: Account[]; engines: EngineReach[]; pages?: SocialPage[];
      }>(await apiFetch(
        `/api/workspaces/${workspaceId}/publishing/integrations/all`,
        { method: "POST", body: JSON.stringify({ confirm_external_action: true }) },
      ));
      setAllAccounts(result.accounts);
      setPages(result.pages ?? []);
      setEngineReach(result.engines);
      setAccountsLoaded(true);
      // Keep what is still there, drop what the refresh no longer returns: a
      // destination that has gone would otherwise be submitted and rejected by
      // the engine rather than here.
      setTargets((current) => current.filter(
        (id) => result.accounts.some((account) => account.id === id)));
      const reachable = result.engines.filter((engine) => engine.reachable);
      if (!options.quiet || !result.accounts.length) {
        setNotice(result.accounts.length
          ? `${result.accounts.length} connected account${result.accounts.length === 1 ? "" : "s"} across ${reachable.length} engine${reachable.length === 1 ? "" : "s"}.`
          : "No connected accounts yet. Connect them in an engine's dashboard, then refresh.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not refresh connected accounts.");
    } finally {
      setBusy(null);
    }
  }

  async function submit(formElement: HTMLFormElement, execute: boolean) {
    if (!workspaceId) return;
    setBusy(execute ? "publish" : "preview");
    setError(null);
    setNotice(null);
    try {
      const body = requestFrom(new FormData(formElement), execute);
      if (execute) {
        await json(await apiFetch(`/api/workspaces/${workspaceId}/publishing/jobs`, { method: "POST", body: JSON.stringify(body) }));
        await refreshJobs();
        // The post has been handed to the engine, so it is no longer a draft
        // and should not reappear the next time this page loads.
        setCaption("");
        setTitle("");
        setFirstComment("");
        setThread([]);
        setPreview(null);
        setNotice("Publishing job created. Track its status below or from Jobs.");
      } else {
        const result = await json<{ preview: Preview }>(await apiFetch(
          `/api/workspaces/${workspaceId}/publishing/preview`,
          { method: "POST", body: JSON.stringify(body) },
        ));
        setPreview(result.preview);
        setNotice("Dry-run ready. Nothing was sent — review the plan, then publish.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Publishing request failed.");
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <main className="publish-page"><p>{t("publish.checkingSession")}</p></main>;
  if (!user) return <main className="publish-page"><Link className={buttonClass({ variant: "primary" })} href="/sign-in?next=%2Fpublish">{t("publish.signInPrompt")}</Link></main>;

  return (
    <main className="publish-page">
      <WorkspaceSectionNav area="publish" />
      <header className="publish-heading">
        <div>
          <p className="eyebrow">{t("publish.eyebrow")}</p>
          <h1>{t("publish.heading")}</h1>
          <p className="lede">
            Write the post, choose where it goes, and dry-run it before anything leaves this
            machine. Engine keys live in your local <code>.env</code>.
          </p>
        </div>
        <div className="publish-heading-side">
          {!usableEngines.length && (
            <span className="connection-badge">{checking ? "Checking…" : "No engine"}</span>
          )}
        </div>
      </header>

      <div className="publish-feedback" aria-live="polite">
        {notice && <p className="registry-message">{notice}</p>}
        {error && <p className="registry-error" role="alert">{error}</p>}
      </div>

      {/* Every connected engine, not the active one. A post can go out through
          all of them at once, so a summary naming one made the others look
          switched off - and hid the fact that their destinations were already
          in the picker below. */}
      {usableEngines.length > 0 && !setupOpen ? (
        <div className="engine-summary">
          <span className="engine-summary-marks">
            {(switchedOnEngines.length ? switchedOnEngines : usableEngines).map((provider) => (
              <ProviderMark key={provider.id} provider={provider.id} size={22} />
            ))}
          </span>
          <div>
            <strong>{switchedOnEngines.length
              ? engineNames(switchedOnEngines)
              : t("publish.noEngineOn")}</strong>
            <span>
              {/* Three different situations, not one. Nothing switched on is a
                  switch to flip; switched on with no destinations is channels
                  to connect at the engine. Collapsing them sent you to a
                  control that was already in the right position. */}
              {!switchedOnEngines.length
                ? t("publish.noEngineOnHelp")
                : accounts.length
                  ? t("publish.destinationsAcross", {
                      destinations: accounts.length, engines: switchedOnEngines.length,
                    })
                  : connection?.next_step}
            </span>
          </div>
          {hosting?.required && !hosting.configured && (
            <Badge tone="warn">{t("publish.mediaHostingNeeded")}</Badge>
          )}
          {usableEngines.map((provider) => (
            <a
              key={provider.id}
              className={buttonClass({ variant: "quiet", size: "sm" })}
              href={provider.dashboard_url}
              target="_blank"
              rel="noopener noreferrer"
            >{provider.label}</a>
          ))}
          <Button variant="quiet" size="sm" onClick={() => setSetupOpen(true)}>
            {t("publish.engineSetup")}
          </Button>
        </div>
      ) : (
      <section className="engine-setup" aria-labelledby="engine-setup-title">
        <div className="section-heading">
          <div>
            <h2 id="engine-setup-title">{t("publish.chooseEngine")}</h2>
          </div>
          <div className="section-heading-aside">
            {connection && <span>{connection.next_step}</span>}
            {usableEngines.length > 0 && (
              <Button variant="quiet" size="sm" onClick={() => setSetupOpen(false)}>
                Done
              </Button>
            )}
          </div>
        </div>
        <div className="engine-grid">
          {connection?.providers.map((provider) => {
            const isDefault = provider.id === connection.active_provider;
            const status = engineState(provider);
            // Whether this engine could deliver if asked. Whether it should is
            // the switch below, and the two are deliberately not the same test.
            const usable = ["ready", "no-accounts"].includes(status.state);
            const open = openProvider === provider.id;
            return (
              <article
                className={`engine-card engine-${status.state}`}
                key={provider.id}
                style={{ "--engine-accent": provider.accent } as React.CSSProperties}
              >
                <div className="engine-card-head">
                  <ProviderMark provider={provider.id} />
                  <div>
                    <strong>{provider.label}</strong>
                    <span>{provider.tagline}</span>
                  </div>
                  {/* One word for the state, and the switch beside it, so
                      "can this engine publish?" and "should it?" are answered
                      in the same glance rather than inferred from a key field
                      three lines down. */}
                  <Badge tone={status.tone}>{t(`publish.engineState.${status.state}`)}</Badge>
                </div>
                <p className="engine-blurb">{provider.summary}</p>
                <div className="engine-platforms" aria-label={t("publish.engineSupports", {
                  label: provider.label, count: provider.platforms.length,
                })}>
                  {provider.platforms.map((platform) => (
                    <span key={platform} title={platformLabels[platform]}>
                      <PlatformIcon platform={platform} size={16} muted={!usable || engineOff(provider.id)} />
                    </span>
                  ))}
                  <em>{provider.platforms.length}</em>
                </div>
                {/* The engine's own message where there is one, and the thing
                    to do about it either way. A state without a next step is a
                    dead end dressed as information. */}
                <p className={`engine-status-line${status.tone === "bad" ? " bad" : ""}`} role="status">
                  <span>{status.detail}</span>
                  {status.fix && <small>{status.fix}</small>}
                  {/* The link the sentence above just sent you to. "Reconnect
                      the account in the engine's dashboard" without a way to
                      get there is an instruction, not a fix - and the page it
                      means differs by state: a refused key wants the keys page,
                      no channels wants the channels page. */}
                  {(status.state === "rejected" || status.state === "no-accounts") && (
                    <a
                      className="engine-status-link"
                      href={status.state === "no-accounts"
                        ? provider.channels_url : provider.dashboard_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >{t(status.state === "no-accounts"
                      ? "publish.openChannels" : "publish.openKeys", {
                        label: provider.label,
                      })}</a>
                  )}
                </p>
                {/* Its own row. The switch answers "will this engine carry the
                    post", which is a different question from the three key
                    actions below it - and as a peer of those buttons it needed
                    an auto margin that broke the row onto two lines. */}
                {/* What the plan allows and how much is gone, under the state
                    it qualifies. Three confidences, and the difference between
                    them is not decoration: a figure read from the engine and a
                    figure quoted from a pricing page are worth different
                    amounts, and showing them alike is how a year-old scrape
                    gets reconciled against a bill. */}
                {(engineReach.find((item) => item.id === provider.id)?.allowances ?? [])
                  .length > 0 && (
                  <ul className="engine-allowances">
                    {(engineReach.find((item) => item.id === provider.id)?.allowances ?? [])
                      .map((item) => {
                        const share = item.limit && item.used !== null
                          ? Math.min(100, Math.round((item.used / item.limit) * 100))
                          : null;
                        const tight = share !== null && share >= 80;
                        return (
                          <li key={item.id} title={item.note}>
                            <span className="engine-allowance-label">
                              {item.label}
                              <em className={`engine-confidence ${item.confidence}`}>
                                {t(`publish.confidence.${item.confidence}`)}
                              </em>
                            </span>
                            <span className={`engine-allowance-figure${tight ? " tight" : ""}`}>
                              {item.unlimited
                                ? t("publish.noLimit")
                                : item.used === null
                                  ? t("publish.limitOnly", { limit: item.limit ?? 0 })
                                  : t("publish.usedOfLimit", {
                                      used: item.used, limit: item.limit ?? 0,
                                    })}
                            </span>
                            {/* Only where both numbers are real. A bar drawn
                                from a limit with no usage would imply a
                                measurement that was never taken. */}
                            {share !== null && (
                              <span className="engine-allowance-bar" aria-hidden="true">
                                <i style={{ inlineSize: `${share}%` }} />
                              </span>
                            )}
                          </li>
                        );
                      })}
                  </ul>
                )}

                <div className="engine-switch-row">
                  {/* Intent, not capability.
                   *
                   * These were one control and should not have been. A refused
                   * key made the switch disabled, so an engine you had no
                   * intention of using could not be switched off - and the
                   * picker went on listing it as something to fix, with no way
                   * to say "I am not using this one".
                   *
                   * The switch now means "I want to publish through this". The
                   * badge above says whether it currently can. Turning on a
                   * broken engine is allowed; it contributes nothing until it
                   * is fixed, and the line under it says why. */}
                  <Switch
                    checked={!engineOff(provider.id)}
                    disabled={!canExecute}
                    label={t("publish.useForPublishing")}
                    onChange={(next) => setDisabledEngines(
                      next
                        ? disabledEngines.filter((id) => id !== provider.id)
                        : [...disabledEngines, provider.id],
                    )}
                  />
                  {isDefault
                    ? <Badge tone="accent">{t("publish.defaultEngine")}</Badge>
                    : <Button
                        variant="quiet"
                        size="sm"
                        disabled={!canExecute || !provider.configured}
                        busy={busy === `${provider.id}-activate`}
                        title={provider.configured
                          ? t("publish.defaultEngineHelp")
                          : t("publish.saveKeyFirst")}
                        onClick={() => void activateProvider(provider)}
                      >{busy === `${provider.id}-activate`
                        ? t("publish.switching")
                        : t("publish.makeDefault")}</Button>}
                </div>
                <div className="engine-actions">
                  <Button
                    variant="quiet"
                    size="sm"
                    disabled={busy !== null || !provider.configured}
                    busy={busy === `${provider.id}-test`}
                    onClick={() => void testProvider(provider)}
                  >{busy === `${provider.id}-test` ? "Testing" : "Test key"}</Button>
                  <Button
                    variant="quiet"
                    size="sm"
                    aria-expanded={open}
                    onClick={() => setOpenProvider(open ? null : provider.id)}
                  >{open ? "Close" : provider.configured ? "Replace key" : "Add key"}</Button>
                  {/* Always available, not only when something is wrong: the
                      engine's own dashboard is where channels are connected and
                      posts are reviewed, which are ordinary errands. */}
                  <a
                    className={buttonClass({ variant: "quiet" })}
                    href={provider.dashboard_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >{t("publish.openDashboard")}</a>
                  <a className={buttonClass({ variant: "quiet" })} href={provider.docs_url} target="_blank" rel="noopener noreferrer">{t("publish.docs")}</a>
                </div>
                {open && (
                  <div className="engine-credentials">
                    {provider.credential_fields.map((field) => (
                      <label key={field.id}>
                        <span>
                          {field.label}
                          <b className={field.configured ? "configured" : "missing"}>
                            {field.configured ? "configured" : field.required ? "required" : "optional"}
                          </b>
                        </span>
                        <input
                          autoComplete={field.secret ? "new-password" : "off"}
                          disabled={!canExecute}
                          onChange={(event) => setCredentialDrafts((current) => ({
                            ...current,
                            [provider.id]: { ...current[provider.id], [field.id]: event.target.value },
                          }))}
                          placeholder={field.configured ? "Enter a new value to replace" : `Paste ${field.label.toLowerCase()}`}
                          spellCheck={false}
                          type={field.secret ? "password" : "text"}
                          value={credentialDrafts[provider.id]?.[field.id] ?? ""}
                        />
                        <small>{field.help} Stored as <code>{field.key}</code>.</small>
                      </label>
                    ))}
                    <div className="engine-credential-actions">
                      <Button
                        variant="primary"
                        disabled={!canExecute}
                        busy={busy === `${provider.id}-credentials`}
                        onClick={() => void saveCredentials(provider, !isDefault)}
                      >
                        {busy === `${provider.id}-credentials`
                          ? t("publish.saving")
                          : isDefault ? t("publish.saveToEnv") : t("publish.saveAndUse")}
                      </Button>
                      <a className={buttonClass({ variant: "quiet" })} href={provider.dashboard_url} target="_blank" rel="noopener noreferrer">
                        Get a key
                      </a>
                    </div>
                    <p className="privacy-note">
                      Written only to this machine&apos;s local <code>.env</code>. Saved values are
                      never sent back to this page.
                    </p>
                  </div>
                )}
              </article>
            );
          })}
        </div>
        {hosting && (
          <article className={`hosting-card${hosting.configured ? " ready" : hosting.required ? " needed" : ""}`}>
            <div className="hosting-head">
              <div>
                <h3>{t("publish.mediaHosting")} <span>{hosting.label}</span></h3>
                <p>
                  {hosting.configured
                    ? "Local clips are uploaded automatically for engines that fetch rather than accept an upload."
                    : hosting.required
                      ? `${fetchingNames || activeProvider?.label} downloads your video instead of accepting an upload, so it needs a public URL. Add storage and TrendRelay will host the file for you.`
                      : "Not needed by the engines this post uses. Add it as soon as one destination goes through an engine that fetches media, such as Buffer."}
                </p>
              </div>
              <div className="hosting-status">
                <Badge tone={hosting.configured ? "good" : "neutral"}>
                  {hosting.configured ? "configured" : "not set up"}
                </Badge>
                {/* Every field below names a path inside this page. Linking to
                    it is the difference between following the instructions and
                    hunting for where they start. */}
                {hosting.dashboard_url && (
                  <a
                    className={buttonClass({ variant: "quiet", size: "sm" })}
                    href={hosting.dashboard_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >{t("publish.openHostingDashboard", { label: hosting.label })}</a>
                )}
                <Button
                  variant="quiet"
                  size="sm"
                  aria-expanded={hostingOpen}
                  onClick={() => setHostingOpen(!hostingOpen)}
                >{hostingOpen ? t("common.close") : hosting.configured
                  ? t("publish.replaceKeys") : t("publish.setUp")}</Button>
              </div>
            </div>
            {hostingOpen && (
              <div className="engine-credentials">
                {hosting.credential_fields.map((field) => (
                  <label key={field.id}>
                    <span>
                      {field.label}
                      <b className={field.configured ? "configured" : "missing"}>
                        {field.configured ? "configured" : field.required ? "required" : "optional"}
                      </b>
                    </span>
                    <input
                      autoComplete={field.secret ? "new-password" : "off"}
                      disabled={!canExecute}
                      onChange={(event) => setHostingDraft((current) => ({
                        ...current,
                        [field.id]: event.target.value,
                      }))}
                      placeholder={field.configured ? "Enter a new value to replace" : `Paste ${field.label.toLowerCase()}`}
                      spellCheck={false}
                      type={field.secret ? "password" : "text"}
                      value={hostingDraft[field.id] ?? ""}
                    />
                    <small>{field.help} Stored as <code>{field.key}</code>.</small>
                  </label>
                ))}
                <div className="engine-credential-actions">
                  <Button
                    variant="primary"
                    disabled={!canExecute}
                    busy={busy === "hosting-credentials"}
                    onClick={() => void saveHosting()}
                  >{busy === "hosting-credentials" ? "Saving" : "Save to .env"}</Button>
                  <a className={buttonClass({ variant: "quiet" })} href="https://dash.cloudflare.com/?to=/:account/r2" target="_blank" rel="noopener noreferrer">
                    Open R2
                  </a>
                </div>
                <p className="privacy-note">
                  Uploaded files are readable by anyone holding the link, which is what lets the
                  engine fetch them. Use a bucket kept for publishing, and note that a clip with a
                  blurred version always uploads the blurred cut.
                </p>
              </div>
            )}
          </article>
        )}
        {!canExecute && selected && (
          <p className="setup-note">
            Only workspace owners and approvers can change engines, save keys, or publish.
            You can still review the setup and dry-run a delivery.
          </p>
        )}
      </section>
      )}

      <section className="publish-layout">
        <form className="publish-form" onSubmit={(event) => { event.preventDefault(); void submit(event.currentTarget, false); }}>
          <div className="section-heading">
            <div>
              <h2>{t("publish.whatGoesOut")}</h2>
            </div>
            {/* Every engine carrying part of this post, not one active one.
                Which engine delivers where is the thing you most need to see
                when a post spans several. */}
            <span>{deliveringNames
              ? `via ${deliveringNames}`
              : activeProvider
                ? `via ${activeProvider.label}`
                : "no destinations chosen"}</span>
          </div>

          <label>{t("workspace.select")}
            <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} required>
              {workspaces.map((workspace) => <option key={workspace.id} value={workspace.id}>{workspace.name} / {workspace.role}</option>)}
            </select>
          </label>

          {/* Where it goes, before what it says.

              The destinations decide the caption limit, which post types
              are offered, and whether the affiliate link can be a link at
              all. Asking for the caption first meant writing to a limit
              nobody had been told yet. */}
          <fieldset className="account-picker">
            <legend>
              Destinations
              {/* Accounts, not networks. Two TikTok accounts are two posts, and
                  counting networks made choosing the second look like it had
                  done nothing. */}
              <b>{targets.length
                ? t("publish.destinationCount", {
                    pages: targets.length, networks: chosen.length,
                  })
                : t("publish.noneSelected")}</b>
            </legend>
            {!connection?.authenticated ? (
              <p className="picker-empty">
                Save and activate an engine key above, then load its connected accounts.
              </p>
            ) : !accounts.length ? (
              <div className="picker-empty">
                <p>{accountsLoaded
                  ? "No engine returned a connected account. Connect channels in an engine's dashboard, then load again."
                  : "No destinations loaded yet."}</p>
                <Button
                  variant="quiet"
                  disabled={!canExecute}
                  busy={busy === "accounts"}
                  onClick={() => void refreshAccounts()}
                ><ActionIcon name="refresh" />{busy === "accounts" ? "Loading" : "Load connected accounts"}</Button>
              </div>
            ) : (
              <>
                {/* One line, not one block per engine. What is needed here is
                    which engines are missing and why in a word; the sentence
                    explaining it and the remedy are already on the engine's own
                    card, and repeating them verbatim pushed the destinations
                    themselves off the screen. */}
                {unavailableEngines.length > 0 && (
                  <p className="engine-note" role="status">
                    {t("publish.notOffering", {
                      engines: unavailableEngines
                        .map(({ provider, status }) =>
                          `${provider.label} (${t(`publish.engineState.${status.state}`)})`)
                        .join(", "),
                    })}{" "}
                    <button type="button" className="link-action" onClick={() => setSetupOpen(true)}>
                      {t("publish.engineSetup")}
                    </button>
                  </p>
                )}
                {/* Switched off here, not broken. Said plainly so a missing
                    account is never a mystery. */}
                {disabledEngines.length > 0 && (
                  <p className="engine-note">
                    {t("publish.enginesOff", {
                      engines: (connection?.providers ?? [])
                        .filter((item) => disabledEngines.includes(item.id))
                        .map((item) => item.label)
                        .join(", "),
                    })}{" "}
                    <button type="button" className="link-action" onClick={() => setSetupOpen(true)}>
                      {t("publish.engineSetup")}
                    </button>
                  </p>
                )}
                <div className="platform-grid">{connectedPlatforms.map((platform) => {
                  const platformPages = offeredPages.filter((page) => page.platform === platform);
                  if (!platformPages.length) return null;
                  const picked = platformPages.filter(pageChosen);
                  return (
                    <section key={platform} className={`platform-card${picked.length ? " chosen" : ""}`}>
                      <div className="platform-card-head">
                        <PlatformIcon platform={platform} />
                        <div>
                          <strong>{platformLabels[platform]}</strong>
                          <span>
                            {picked.length
                              ? t("publish.pagesChosen", {
                                  chosen: picked.length, total: platformPages.length,
                                })
                              : t("publish.pagesConnected", { count: platformPages.length })}
                          </span>
                        </div>
                        {/* One reach-everything action per network. Choosing
                            eight pages one at a time is the work this page
                            exists to remove. */}
                        {platformPages.length > 1 && (
                          <button
                            type="button"
                            className="platform-card-all"
                            onClick={() => {
                              const all = picked.length === platformPages.length;
                              platformPages.forEach((page) => togglePage(page, !all));
                            }}
                          >{picked.length === platformPages.length
                            ? t("publish.selectNone") : t("publish.selectAll")}</button>
                        )}
                      </div>
                      <div className="account-options">{platformPages.map((page) => {
                        const on = pageChosen(page);
                        const route = routeOf(page);
                        return (
                          <button
                            type="button"
                            key={page.key}
                            aria-pressed={on}
                            className={on ? "selected" : ""}
                            title={page.handle ? `@${page.handle}` : page.label}
                            onClick={() => togglePage(page, !on)}
                          >
                            <span>{page.label}</span>
                            {/* One page, several engines: say so, and say which
                                one is actually delivering. Two rows that look
                                like two accounts is how the same audience gets
                                posted to twice. */}
                            <i>{page.reachable_by.length > 1
                              ? t("publish.viaOneOf", {
                                  provider: route.provider_label,
                                  count: page.reachable_by.length,
                                })
                              : route.provider_label}</i>
                          </button>
                        );
                      })}</div>

                      {/* Only where there is a real choice to make. */}
                      {picked.filter((page) => page.reachable_by.length > 1).map((page) => (
                        <div className="page-route" key={`${page.key}-route`}>
                          <em>{t("publish.deliverVia", { label: page.label })}</em>
                          {page.reachable_by.map((item) => {
                            const active = routeOf(page).id === item.id;
                            return (
                              <button
                                type="button"
                                key={`${item.provider}:${item.id}`}
                                aria-pressed={active}
                                className={active ? "selected" : ""}
                                onClick={() => switchRoute(page, item.provider, item.id)}
                              >{item.provider_label}</button>
                            );
                          })}
                        </div>
                      ))}

                      {/* Per page, not per network: the same post can be a Reel
                          on one Instagram page and a Story on another. */}
                      {picked.map((page) => {
                        const route = routeOf(page);
                        const kinds = postTypesFor(route.id);
                        if (kinds.length < 2) return null;
                        return (
                          <div
                            className="post-types"
                            key={page.key}
                            role="tablist"
                            aria-label={`${page.label} post type`}
                          >
                            {picked.length > 1 && <em className="post-types-for">{page.label}</em>}
                            {kinds.map((kind) => {
                              const active = (postTypes[route.id] ?? kinds[0]?.id) === kind.id;
                              return (
                                <button
                                  type="button"
                                  key={kind.id}
                                  aria-pressed={active}
                                  className={active ? "selected" : ""}
                                  title={kind.help}
                                  onClick={() => setPostTypes({ ...postTypes, [route.id]: kind.id })}
                                >{kind.label}</button>
                              );
                            })}
                          </div>
                        );
                      })}
                    </section>
                  );
                })}</div>
                <div className="picker-footer">
                  {/* Which engines these destinations came from. The list is
                      every engine at once, so naming one would be wrong; and
                      the old line comparing connected to supported platforms
                      could only ever say "no other platforms" once accounts
                      were loaded from all of them. */}
                  {/* Only engines actually contributing destinations. One
                      reachable with nothing on it is already named above as
                      having no channels; listing it here as "Zernio (0)" says
                      the same thing again, in a shape that reads like a total. */}
                  <p>
                    {(() => {
                      const contributing = engineReach.filter(
                        (engine) => engine.reachable && engine.account_count > 0
                          && !engineOff(engine.id));
                      return contributing.length
                        ? t("publish.reachableThrough", {
                            engines: contributing
                              .map((engine) => `${engine.label} (${engine.account_count})`)
                              .join(", "),
                          })
                        : t("publish.nothingReachable");
                    })()}
                  </p>
                  <Button
                    variant="quiet"
                    size="sm"
                    disabled={!canExecute}
                    busy={busy === "accounts"}
                    onClick={() => void refreshAccounts()}
                  >{busy === "accounts" ? "Refreshing" : "Refresh accounts"}</Button>
                </div>
              </>
            )}
          </fieldset>
          {chosen.includes("reddit") && (
            <label>{t("publish.subreddit")}
              <input name="subreddit" placeholder="r/videos" required />
              <small>{t("publish.subredditRequired")}</small>
            </label>
          )}
          {chosen.includes("pinterest") && (
            <label>{t("publish.pinterestBoard")}
              <input name="board" placeholder={t("publish.productLaunches")} required />
              <small>{t("publish.pinterestBoardHelp")}</small>
            </label>
          )}

          {/* The clip, once its constraints are known.

              Whether the media has to be publicly hosted depends on which
              engine delivers it, so asking for it first meant warning about
              a constraint from a destination nobody had chosen. */}
          {needsPublicMedia && !hostsLocalMedia ? (
            <div className="ui-field">
              {/* The picker belongs here too. This engine fetches rather than
                  uploads, but a clip still has to be chosen before anyone can
                  know that hosting is what stands in the way. */}
              <div className="field-with-action">
                <label className="ui-field-label">{t("publish.publicMediaUrl")}
                  <input name="media_url" type="url" value={mediaUrl} onChange={(event) => setMediaUrl(event.target.value)} placeholder="https://cdn.example.com/approved-clip.mp4" required={!videoPath} />
                </label>
                <Button variant="quiet" onClick={openPicker}><ActionIcon name="clip" />{t("publish.chooseFromLibrary")}</Button>
              </div>
              {clip && (
                <span className="chosen-clip">
                  <b>{clip.title}</b>
                  {clip.duration_ms ? <i>{clipLength(clip.duration_ms)}</i> : null}
                  {isBlurred(clip) && <em className="blurred-tag">{t("publish.facesBlurred")}</em>}
                </span>
              )}
              {videoPath && !mediaUrl ? (
                <p className="publish-blocked" role="status">
                  {fetchingNames || activeProvider?.label} downloads the file rather than
                  accepting an upload, so this clip needs somewhere public to sit.{" "}
                  <button type="button" className="link-action" onClick={() => setHostingOpen(true)}>
                    Set up media hosting
                  </button>{" "}
                  and TrendRelay will do it for you, or paste a URL you already host.
                </p>
              ) : (
                <small className="ui-field-note">{mediaNote}</small>
              )}
            </div>
          ) : needsPublicMedia ? (
            <>
              <div className="ui-field">
                <div
                  className={`field-with-action dropzone${dragOver ? " over" : ""}`}
                  onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={dropMedia}
                >
                  <label>{t("publish.approvedPath")}
                    <input name="video_path" value={videoPath} onChange={(event) => setVideoPath(event.target.value)} placeholder=".data\media\approved-clip.mp4" />
                  </label>
                  <Button variant="quiet" onClick={openPicker}><ActionIcon name="clip" />{t("publish.chooseFromLibrary")}</Button>
                </div>
                {clip && (
                  <span className="chosen-clip">
                    <b>{clip.title}</b>
                    {clip.duration_ms ? <i>{clipLength(clip.duration_ms)}</i> : null}
                    {isBlurred(clip) && <em className="blurred-tag">{t("publish.facesBlurred")}</em>}
                  </span>
                )}
                <small className="ui-field-note">
                  Uploaded to {hosting?.label} when the post runs, so {fetchingNames || activeProvider?.label} can
                  fetch it. If the clip has a blurred version, that is the cut that gets uploaded.
                </small>
              </div>
              <label>{t("publish.publicMediaUrl")} <i>{t("publish.optional")}</i>
                <input name="media_url" type="url" value={mediaUrl} onChange={(event) => setMediaUrl(event.target.value)} placeholder="https://cdn.example.com/approved-clip.mp4" />
                <small>{t("publish.supplyHosted")}</small>
              </label>
            </>
          ) : (
            <>
              <div className="ui-field">
                <div
                  className={`field-with-action dropzone${dragOver ? " over" : ""}`}
                  onDragOver={(event) => { event.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={dropMedia}
                >
                  <label>{t("publish.approvedPath")}
                    <input name="video_path" value={videoPath} onChange={(event) => setVideoPath(event.target.value)} placeholder=".data\media\approved-clip.mp4" required />
                  </label>
                  <Button variant="quiet" onClick={openPicker}><ActionIcon name="clip" />{t("publish.chooseFromLibrary")}</Button>
                </div>
                {clip && (
                  <span className="chosen-clip">
                    <b>{clip.title}</b>
                    {clip.duration_ms ? <i>{clipLength(clip.duration_ms)}</i> : null}
                    {isBlurred(clip) && <em className="blurred-tag">{t("publish.facesBlurred")}</em>}
                  </span>
                )}
                <small className="ui-field-note">{mediaNote ?? "Media must sit under a configured publishing media directory."}</small>
              </div>
              <label>{t("publish.publicMediaUrl")} <i>{t("publish.optional")}</i>
                <input name="media_url" type="url" value={mediaUrl} onChange={(event) => setMediaUrl(event.target.value)} placeholder="https://cdn.example.com/approved-clip.mp4" />
                <small>{t("publish.supplySkipUpload")}</small>
              </label>
            </>
          )}

          {/* Only where a chosen destination has a title field. Most posts do
              not, and an always-present input labelled "used by YouTube, Reddit
              and Pinterest" asks every operator to decide whether it applies to
              them - a decision the destinations already answer. */}
          {titleLimit && (
          <label>{t("publish.title")} <i>{t("publish.titleUsedBy")}</i>
            <input name="title" maxLength={300} value={title} onChange={(event) => setTitle(event.target.value)} />
            <small className={`char-count${titleOver > 0 ? " over" : ""}`}>
              {title.length} / {titleLimit.title}
              <i>tightest: {platformLabels[titleLimit.platform as PublishingPlatform]}</i>
            </small>
          </label>
          )}
          <label>{t("publish.caption")}
            <textarea name="caption" rows={5} maxLength={5000} required value={caption} onChange={(event) => setCaption(event.target.value)} />
            {captionLimit && (
              <small className={`char-count${captionOver > 0 ? " over" : captionOver > -20 ? " close" : ""}`}>
                {caption.length.toLocaleString()} / {captionLimit.caption.toLocaleString()}
                <i>tightest: {platformLabels[captionLimit.platform as PublishingPlatform]}</i>
              </small>
            )}
          </label>

          {(() => {
            // Threading is a property of the engine delivering that
            // destination. A network can thread through one engine and not
            // through another, so this is asked per destination.
            // Offered when any engine delivering the network can thread. The
            // note below already says which destinations get the caption only,
            // so the composer appearing is not a promise that all of them will
            // receive the replies.
            const threaders = chosen.filter((platform) =>
              providersFor(platform).some(
                (provider) => (provider.thread_platforms ?? []).includes(platform)));
            if (!threaders.length && !thread.length) return null;
            // The strictest engine among the ones threading: exceeding its
            // limit is rejected by that engine, whatever the others allow.
            const threadProviders = threaders.flatMap((platform) => providersFor(platform));
            const limit = threaders.length
              ? Math.min(...threaders.flatMap((platform) => providersFor(platform)
                  .map((provider) => provider.limits?.[platform]?.caption ?? 2200)))
              : null;
            const maxParts = threadProviders.length
              ? Math.min(...threadProviders.map((provider) => provider.max_thread_parts ?? 25))
              : (activeProvider?.max_thread_parts ?? 25);
            return (
              <div className="thread-composer">
                <div className="thread-head">
                  <strong>{t("publish.thread")}</strong>
                  <span>
                    {thread.length
                      ? `${thread.length + 1} posts on ${threaders.map((p) => platformLabels[p]).join(", ")}`
                      : `Add replies for ${threaders.map((p) => platformLabels[p]).join(", ")}`}
                  </span>
                </div>
                {thread.map((part, index) => {
                  const over = limit ? part.length - limit : 0;
                  return (
                    <div className="thread-part" key={index}>
                      <span className="thread-index">{index + 2}</span>
                      <div>
                        <textarea
                          rows={2}
                          value={part}
                          placeholder={`Reply ${index + 1}`}
                          onChange={(event) => setThread(thread.map(
                            (item, at) => (at === index ? event.target.value : item)))}
                        />
                        {limit && (
                          <small className={`char-count${over > 0 ? " over" : ""}`}>
                            {part.length} / {limit}
                          </small>
                        )}
                      </div>
                      <button
                        type="button"
                        className="slot-remove"
                        aria-label={`Remove reply ${index + 1}`}
                        onClick={() => setThread(thread.filter((_item, at) => at !== index))}
                      >×</button>
                    </div>
                  );
                })}
                <Button
                  variant="quiet"
                  size="sm"
                  disabled={thread.length >= maxParts - 1}
                  onClick={() => setThread([...thread, ""])}
                >{t("publish.addReply")}</Button>
                {/* Each part is its own post, so the limit is per part - which
                    is the opposite of how a single caption is counted. */}
                {thread.length > 0 && chosen.length > threaders.length && (
                  <small className="thread-note">
                    {chosen.filter((p) => !threaders.includes(p))
                      .map((p) => platformLabels[p]).join(", ")}{" "}
                    will receive the caption only.
                  </small>
                )}
              </div>
            );
          })()}

          {(() => {
            const carriers = chosen.filter((platform) =>
              providersFor(platform).some(
                (provider) => (provider.first_comment_platforms ?? []).includes(platform)));
            if (!carriers.length) return null;
            return (
              <label>{t("publish.firstComment")} <i>{t("publish.optional")}</i>
                <textarea
                  name="first_comment"
                  rows={2}
                  maxLength={2000}
                  placeholder={t("publish.hashtagsHint")}
                  value={firstComment}
                  onChange={(event) => setFirstComment(event.target.value)}
                />
                <small>
                  Posted as a reply straight after the post on{" "}
                  {carriers.map((platform) => platformLabels[platform]).join(", ")}.
                  {chosen.length > carriers.length
                    && " The other destinations do not take one and will be skipped."}
                </small>
              </label>
            );
          })()}

          <div className="delivery-mode" role="group" aria-label={t("publish.deliveryMode")}>
            {([
              ["draft", "Save as draft", "Nothing publishes until you approve it in the engine"],
              ["schedule", "Schedule", "The engine publishes automatically at the time below"],
              ["now", "Publish now", "Goes live as soon as the engine accepts it"],
            ] as const).map(([mode, title, hint]) => (
              <button
                key={mode}
                type="button"
                className={`delivery-option${delivery === mode ? " selected" : ""}`}
                aria-pressed={delivery === mode}
                onClick={() => chooseDelivery(mode)}
              ><strong>{title}</strong><span>{hint}</span></button>
            ))}
          </div>

          <div className="publish-grid">
            <label>{delivery === "schedule" ? "Publish at" : "Reference time"}
              <input
                name="date"
                type="datetime-local"
                required
                min={delivery === "schedule" ? localDateTime(1) : undefined}
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
              <small>
                {delivery === "schedule"
                  ? scheduledInPast
                    ? "That time has passed — choose a later one."
                    : `Goes out ${scheduledAt?.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })} your time.`
                  : delivery === "now"
                    ? "Not used; the post goes out immediately."
                    : "Stored with the draft; the engine does not act on it."}
              </small>
            </label>
            <label>{t("publish.visibility")} <i>{t("publish.visibilityScope")}</i>
              <select name="visibility" defaultValue="public">
                <option value="public">{t("publish.public")}</option>
                <option value="private">{t("publish.privateOnlyMe")}</option>
              </select>
            </label>
          </div>

          {delivery === "schedule" && quickSlots.length > 0 && (
            <div className="time-slots" role="group" aria-label={t("publish.nextPostingTimes")}>
              <span>{t("publish.nextSlots")}</span>
              {nextFree && (
                <button
                  type="button"
                  className={`slot-next${date === nextFree.value ? " selected" : ""}`}
                  aria-pressed={date === nextFree.value}
                  title={t("publish.soonestSlot")}
                  onClick={() => setDate(nextFree.value)}
                ><b>{t("publish.nextFree")}</b><i>{nextFree.day} {nextFree.label}</i></button>
              )}
              {quickSlots.map((slot) => (
                <button
                  key={slot.value}
                  type="button"
                  aria-pressed={date === slot.value}
                  className={date === slot.value ? "selected" : ""}
                  onClick={() => setDate(slot.value)}
                ><b>{slot.label}</b><i>{slot.day}</i></button>
              ))}
            </div>
          )}

          {delivery === "schedule" && (
            <div className="schedule-planner">
              {slots.length ? (
                <WeekCalendar
                  slots={slots}
                  entries={scheduled}
                  selected={date}
                  now={now}
                  onPick={(at) => setDate(localValue(at))}
                />
              ) : (
                /* An empty week is not a calendar, so the thing that fills it
                   is offered here rather than behind the editor below. */
                <div className="planner-empty">
                  <div>
                    <strong>{t("publish.noTimesYet")}</strong>
                    <span>{t("publish.pickRhythm")}</span>
                  </div>
                  <div className="planner-empty-presets">
                    {slotPresets.map((preset) => (
                      <Button
                        key={preset.id}
                        variant="secondary"
                        size="sm"
                        busy={busy === "slots"}
                        disabled={!canExecute}
                        title={preset.summary}
                        onClick={() => void saveSlots(
                          preset.times.map((time) => ({ weekday: -1, time })))}
                      >{preset.label}</Button>
                    ))}
                  </div>
                </div>
              )}
              <details className="planner-editor">
                <summary>
                  Posting times
                  <b>{slots.length ? `${slots.length} per day` : "none set"}</b>
                </summary>
                <SlotEditor
                  slots={slots}
                  presets={slotPresets}
                  timezone={timezone}
                  canEdit={Boolean(canExecute)}
                  busy={busy === "slots"}
                  onSave={(entries) => void saveSlots(entries)}
                />
              </details>
            </div>
          )}


          {approvers.length > 0 && delivery === "draft" && (
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={needsApproval}
                onChange={(event) => setNeedsApproval(event.target.checked)}
              /> {t("publish.sendForApproval")}
              <small>
                Held in {engineNames(approvers)} for a teammate to approve. Only works
                where that channel&apos;s posting policy asks for approval, and only for
                the destinations those engines deliver.
              </small>
            </label>
          )}

          <label className="checkbox-row">
            <input name="made_with_ai" type="checkbox" /> {t("publish.discloseAi")}
            <small>Sets each platform&apos;s synthetic-media flag where the engine exposes one.</small>
          </label>

          {blockedReason && (
            <p className="publish-blocked" role="status">{blockedReason}</p>
          )}
          <div className="publish-actions">
            <Button type="submit" variant="secondary" busy={busy === "preview"} disabled={busy !== null}>
              <ActionIcon name="confirm" />{busy === "preview" ? "Checking" : "Dry-run"}
            </Button>
            <Button
              variant={delivery === "now" ? "danger" : "primary"}
              busy={busy === "publish"}
              disabled={busy !== null || Boolean(blockedReason)}
              title={blockedReason ?? undefined}
              onClick={(event) => {
                const form = event.currentTarget.form;
                const where = chosen.map((platform) => platformLabels[platform]).join(", ");
                if (form && window.confirm(
                  `${{ now: "Publish immediately", schedule: "Schedule", draft: "Create a draft" }[delivery]} on ${deliveringNames || activeProvider?.label} for ${where}?`,
                )) void submit(form, true);
              }}
            >
              {/* The icon follows the mode, since these are three different
                  commitments wearing one button. */}
              <ActionIcon name={delivery === "now" ? "publish" : delivery === "schedule" ? "campaign" : "edit"} />
              {busy === "publish" ? "Submitting" : delivery === "now" ? "Publish now" : delivery === "schedule" ? "Confirm and schedule" : "Confirm and draft"}
            </Button>
          </div>
        </form>

        <aside className="publish-side">
          {/* First in the rail because it answers the question people arrive
              with — what is already going out — before the composer's own
              rehearsal of what they are writing now. */}
          <UpcomingPosts
            entries={scheduled}
            slots={slots}
            now={now}
            onPickDay={(at) => { setDelivery("schedule"); setDate(localValue(at)); }}
            onOpenCalendar={() => {
              setDelivery("schedule");
              queueMicrotask(() => {
                document
                  .querySelector(".schedule-planner")
                  ?.scrollIntoView({ behavior: "smooth", block: "center" });
              });
            }}
          />
          <article className="publish-media-preview">
            <h2>{t("publish.whatWillBeSent")}</h2>
            {videoPath || mediaUrl ? (
              <>
                <video
                  className="blur-preview"
                  controls
                  preload="metadata"
                  src={mediaUrl || `${apiBaseUrl()}/api/workspaces/${workspaceId}/media/library/face-blur/media?path=${encodeURIComponent(videoPath)}`}
                />
                <p className="privacy-note">
                  {mediaUrl
                    ? "Streaming the public URL the engine will fetch."
                    : "Playing the local file this delivery will upload. If a blurred "
                      + "version replaced the original, this is the blurred one."}
                </p>
              </>
            ) : (
              <p>{t("publish.chooseMediaFirst")}</p>
            )}
          </article>
          {previewPlatform && (
            <article>
              <h2>{t("publish.howItWillLook")}</h2>
              <PostPreview
                platform={previewPlatform}
                postTypeLabel={previewType?.label ?? "Post"}
                handle={previewHandle}
                caption={caption}
                title={title}
                thumbnail={thumbnail}
              />
              <p className="privacy-note">
                A rehearsal of the caption and frame against this network&apos;s shape,
                not a render of what {deliveringNames || activeProvider?.label} will produce.
              </p>
            </article>
          )}
          {preview && (
          <article>
            <h2>{t("publish.dryRunPlan")}</h2>
            {(
              <div className="preview-card">
                <p className="preview-lead">
                  <strong>{preview.delivery === "draft" ? "Draft" : "Scheduled post"}</strong> via {preview.provider_label}
                </p>
                <dl className="preview-facts">
                  <div><dt>{t("publish.when")}</dt><dd>{new Date(preview.date).toLocaleString()}</dd></div>
                  <div><dt>{t("publish.media")}</dt><dd>{preview.media_source}</dd></div>
                  <div><dt>{t("publish.visibility")}</dt><dd>{preview.visibility}</dd></div>
                  {preview.made_with_ai && <div><dt>{t("publish.disclosure")}</dt><dd>{t("publish.aiGenerated")}</dd></div>}
                </dl>
                <ul className="preview-destinations">
                  {preview.destinations.map((destination) => (
                    <li key={destination.platform}>
                      <PlatformIcon platform={destination.platform} size={18} />
                      <div>
                        <strong>
                          {destination.label}
                          <em>{destination.post_type_label}</em>
                        </strong>
                        {destination.notes.map((note) => <span key={note}>{note}</span>)}
                      </div>
                    </li>
                  ))}
                </ul>
                <p className="privacy-note">Nothing has been sent. {preview.media_handling}</p>
              </div>
            )}
          </article>
          )}
          <article className="publish-history">
            <details>
              <summary>
                <h2>{t("publish.jobs")}</h2>
                <b>{t("publish.jobCount", { count: jobs.length })}</b>
              </summary>
            {jobs.length ? (
              <div className="record-list">{jobs.slice(0, 8).map((job) => {
                const request = job.payload?.request;
                const where = (request?.targets ?? [])
                  .map((target: { platform: PublishingPlatform }) => platformLabels[target.platform])
                  .filter(Boolean)
                  .join(", ");
                return (
                  <div key={job.id}>
                    <strong>{request?.caption ?? job.id}</strong>
                    <span className="job-line">
                      <Badge tone={
                        job.status === "succeeded" ? "good"
                          : job.status === "failed" ? "bad"
                            : "neutral"
                      }>{job.status}</Badge>
                      {where && <i>{where}</i>}
                      {job.created_at && <time dateTime={job.created_at}>{sinceLabel(job.created_at)}</time>}
                    </span>
                    {job.error && <small className="job-error-line">{job.error}</small>}
                  </div>
                );
              })}</div>
            ) : <p>{t("publish.noJobs")}</p>}
            </details>
          </article>
        </aside>
      </section>
      {workspaceId && (
        <MediaPicker
          open={pickerOpen}
          assets={library}
          workspaceId={workspaceId}
          apiFetch={apiFetch}
          loading={libraryState.loading}
          failure={libraryState.failure}
          facets={libraryFacets}
          onSearch={(filters) => void loadLibrary(filters)}
          onPick={pickClip}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </main>
  );
}
