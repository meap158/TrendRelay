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
