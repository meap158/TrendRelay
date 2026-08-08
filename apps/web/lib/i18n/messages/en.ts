/**
 * The source language. Every other dictionary is a translation of this file,
 * and its keys are the contract: a missing key anywhere falls back to the
 * English here rather than showing a raw key to a user.
 *
 * Keys are grouped by where they appear, and named for what the text *means*
 * rather than what it says, so rewording the English does not orphan six
 * translations.
 */

export const en = {
  app: {
    name: "TrendRelay",
    tagline: "Discover what is trending. Research what matters.",
  },

  nav: {
    discover: "Discover",
    library: "Library",
    studio: "Studio",
    campaigns: "Campaigns",
    publish: "Publish",
    catalog: "Catalog",
    attribution: "Attribution",
    opportunities: "Opportunities",
    tools: "Tools",
    workspaces: "Workspaces",
    about: "About",
    signIn: "Sign in",
    signOut: "Sign out",
    language: "Language",
    chooseLanguage: "Choose a language",
  },

  common: {
    save: "Save",
    cancel: "Cancel",
    close: "Close",
    delete: "Delete",
    confirm: "Confirm",
    retry: "Retry",
    reload: "Reload",
    refresh: "Refresh",
    loading: "Loading…",
    search: "Search",
    filter: "Filter",
    clear: "Clear",
    selectAll: "Select all",
    none: "None",
    all: "All",
    open: "Open",
    download: "Download",
    preview: "Preview",
    apply: "Apply",
    back: "Back",
    next: "Next",
    yes: "Yes",
    no: "No",
    optional: "Optional",
    required: "Required",
    unavailable: "Unavailable",
    comingSoon: "Coming soon",
  },

  status: {
    queued: "Queued",
    running: "Running",
    succeeded: "Succeeded",
    failed: "Failed",
    partial: "Partly done",
    cancelled: "Cancelled",
    ready: "Ready",
    setupRequired: "Setup required",
  },

  workspace: {
    loading: "Loading workspace…",
    loadingHelp:
      "This should take a moment. If it does not, the API may not be running.",
    none: "No workspace yet",
    select: "Workspace",
  },

  discover: {
    title: "Douyin hot search",
    subtitleEmpty:
      "What is trending on Douyin right now, from your connected session.",
    read: "Read the board",
    reading: "Reading…",
    gallery: "Gallery",
    list: "List",
    boardLayout: "Board layout",
    downloadPerTopic: "Download {count} per topic",
    downloadCount: "Download {count}",
    browse: "Browse",
    queueing: "Queueing…",
    heat: "{value} heat",
    views: "{value} views",
    emptyBoard: "The board came back empty. Try again shortly.",
    topicQueued:
      "Queued {count, plural, one {# video} other {# videos}} for “{term}”. Watch it in Downloads.",
    topicFailed: "The topic could not be fetched.",
    boardTermsNeedNoAccount: "Terms from the board above do not need one.",
    connectAccount: "Connect an account in Tools",
  },

  downloads: {
    heading: "Download from Douyin",
    eyebrowAcquisition: "MEDIA ACQUISITION",
    intro:
      "Paste videos, profiles, or collections. TrendRelay downloads them in the background and adds the files to your library.",
    signInPrompt: "Sign in to manage media.",
    signInIntro:
      "Fetch source videos, prepare clips, and send approved posts from one workspace.",
    tryAgain: "Try again",
    workspaceFirst: "Create a workspace first",
    workspaceOwns: "A workspace owns media, approvals, and publishing history.",
    createWorkspace: "Create workspace",
    step: "STEP {number}",
    addLinks: "Add Douyin links",
    addLinksHelp: "Paste a copied share message or put one link on each line.",
    linksLabel: "Douyin links",
    pasteFromClipboard: "Paste from clipboard",
    readyToDownload: "Ready to download",
    remove: "Remove",
    installProvider: "Install the Douyin downloader",
    installProviderHelp: "Enable the managed provider once, then return here.",
    openTools: "Open Tools",
    refreshSession: "Refresh the Douyin session",
    refreshSessionHelp:
      "Your Douyin session is stored locally. Refresh it only if downloads stop working.",
    options: "Download options",
    fromProfiles: "Content from profiles",
    publishedPosts: "Published posts",
    likedVideos: "Liked videos",
    collections: "Collections",
    musicVideos: "Music videos",
    perSource: "Videos per source",
    whatToFetch: "What to fetch",
    whatToFetchHelp:
      "All videos is the default. TrendRelay keeps paging through the source and skips files already downloaded. Unticking an extra means it is never requested, rather than fetched and thrown away.",
    authorisedOnly: "Only download media you are authorized to retain and reuse.",
    heading2: "Downloads",
    autoUpdate: "Active batches update automatically every few seconds.",
    openSource: "Open source",
    openFolder: "Open folder",
    openLibrary: "Open library",
    noneSaved: "No media files were saved",
    reuseLinks: "Refresh the Douyin session, then reuse these links.",
    openInLibrary: "Open in Library",
    plan: "Plan",
    detectedSources: "Detected Douyin sources",
    refreshList: "Refresh downloads",
    filter: "Filter downloads",
    addCreatorProfile:
      "Add the creator's Douyin profile to the link box so you can fetch their whole catalogue",
  },

  research: {
    results: "Results",
    relevance: "Relevance",
    noSignals: "No signals of this type yet.",
    recent: "Recent research",
    noImage: "no image",
    tiktokRegion: "TikTok region",
    tiktokPeriod: "TikTok period",
    perTopicHelp: "How many videos a topic download takes",
    openTermOnDouyin: "Open this term on Douyin",
  },

  library: {
    opening: "Opening media library…",
    signInPrompt: "Sign in to open Library",
    eyebrow: "Creative intelligence",
    intro:
      "Keep originals immutable and turn reference clips into searchable creative recipes.",
    playPreview: "Play video preview",
    privatePreview: "Loaded privately only when you choose to play it",
    keyboardHint: "← → navigate · Space play/pause",
    searchPlaceholder: "Search titles, hooks, transcripts, or creators…",
    searchLabel: "Search library",
    categories: "Media categories",
    sortLabel: "Sort media",
    groupLabel: "Group library",
    viewLabel: "Library view",
    galleryView: "Gallery view",
    listView: "List view",
    browseMedia: "Browse media",
    browsePreviews: "Browse video previews",
    previousItem: "Previous item",
    nextItem: "Next item",
    previousVideo: "Previous video",
    nextVideo: "Next video",
    previousVideoKey: "Previous video (Left arrow)",
    nextVideoKey: "Next video (Right arrow)",
    whichCut: "Which cut to play",
    blurredExists: "A blurred cut exists and is what handoffs send",
    sortNewest: "Newest",
    sortOldest: "Oldest",
    sortTitle: "Title",
    sortLongest: "Longest",
    group: "Group",
    noGrouping: "No grouping",
    channel: "Channel",
    source: "Source",
    videos: "Videos",
    images: "Images",
    audio: "Audio",
    everyMatchSelected: "every match selected",
    runsInBatches: "runs in batches",
    empty: "No matching media yet.",
    importLocal: "Import a local file",
    filePath: "File path",
    platform: "Platform",
    creator: "Creator",
    publishedAt: "Published at",
    sourceUrl: "Source URL",
    caption: "Caption",
    hashtags: "Hashtags",
    likes: "Likes",
    comments: "Comments",
    shares: "Shares",
    recentIngestion: "Recent ingestion",
    effects: "Effects",
    clipPlan: "Clip plan",
    planCampaign: "Plan campaign",
    prepareToPublish: "Prepare to publish",
    blurSettings: "Blur settings",
    blurSettingsHelp:
      "Check coverage on one frame and set how wide the blur sits",
    effectsHelp: "Stack effects on this asset without touching the original",
    selectToBegin: "Select an asset or import a local file to begin.",
  },

  recipe: {
    heading: "Creative recipe",
    spokenHook: "Spoken hook",
    textHook: "Text hook",
    cta: "CTA",
    product: "Product",
    format: "Format",
    editing: "Editing",
    structure: "Structure",
    keywords: "Keywords",
    empty: "No recipe yet. Add reviewed speech or on-screen text below.",
    reviewedHeading: "Reviewed transcript and analysis",
    reviewedIntro:
      "Paste reviewed speech and on-screen text. TrendRelay derives a searchable, versioned recipe without claiming machine output was human-reviewed.",
    language: "Language",
    productShown: "Product shown",
    creativeFormat: "Creative format",
    reviewedSpeech: "Reviewed speech",
    reviewedText: "Reviewed on-screen text",
    sceneCuts: "Scene cuts (ms)",
    productReveal: "Product reveal (ms)",
    emotionalAngle: "Emotional angle",
    analystNotes: "Analyst notes",
  },

  effects: {
    title: "Editing",
    unavailable: "Not available on this machine",
    licenceRequired: "This needs a licence decision before it can run",
    installHint: "Install the add-on to enable this",
  },

  language: {
    switched: "Language changed to {language}",
  },
};

/**
 * The shape every dictionary must fill, with the *keys* required and the values
 * merely strings.
 *
 * Without the widening, `typeof en` would demand the literal English text and
 * every translation would be a type error. Without the mapping at all, a
 * dictionary could quietly omit half its keys.
 */
type Translated<T> = { [K in keyof T]: T[K] extends string ? string : Translated<T[K]> };

export type Messages = Translated<typeof en>;
