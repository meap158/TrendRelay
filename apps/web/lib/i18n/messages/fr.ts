import type { Messages } from "./en";

/** French. Infinitives for actions and nouns for sections, as French product
 *  interfaces do — "Enregistrer", not "Sauvez". */
export const fr: Messages = {
  app: {
    name: "TrendRelay",
    tagline: "Repérez les tendances. Analysez l'essentiel.",
  },

  nav: {
    discover: "Découvrir",
    library: "Bibliothèque",
    studio: "Studio",
    campaigns: "Campagnes",
    publish: "Publier",
    catalog: "Catalogue",
    attribution: "Attribution",
    opportunities: "Opportunités",
    tools: "Outils",
    workspaces: "Espaces de travail",
    about: "À propos",
    signIn: "Se connecter",
    signOut: "Se déconnecter",
    language: "Langue",
    chooseLanguage: "Choisir une langue",
  },

  common: {
    save: "Enregistrer",
    cancel: "Annuler",
    close: "Fermer",
    delete: "Supprimer",
    confirm: "Confirmer",
    retry: "Réessayer",
    reload: "Recharger",
    refresh: "Actualiser",
    loading: "Chargement…",
    search: "Rechercher",
    filter: "Filtrer",
    clear: "Effacer",
    selectAll: "Tout sélectionner",
    none: "Aucun",
    all: "Tout",
    open: "Ouvrir",
    download: "Télécharger",
    preview: "Aperçu",
    apply: "Appliquer",
    back: "Retour",
    next: "Suivant",
    yes: "Oui",
    no: "Non",
    optional: "Facultatif",
    required: "Obligatoire",
    unavailable: "Indisponible",
    comingSoon: "Bientôt disponible",
  },

  status: {
    queued: "En attente",
    running: "En cours",
    succeeded: "Terminé",
    failed: "Échec",
    partial: "Partiellement terminé",
    cancelled: "Annulé",
    ready: "Prêt",
    setupRequired: "Configuration requise",
  },

  workspace: {
    loading: "Chargement de l'espace de travail…",
    loadingHelp:
      "Cela ne devrait prendre qu'un instant. Sinon, l'API n'est peut-être pas démarrée.",
    none: "Aucun espace de travail",
    select: "Espace de travail",
  },

  discover: {
    title: "Recherches populaires sur Douyin",
    subtitleEmpty:
      "Les tendances du moment sur Douyin, d'après votre session connectée.",
    read: "Charger le classement",
    reading: "Chargement…",
    gallery: "Grille",
    list: "Liste",
    boardLayout: "Affichage",
    downloadPerTopic: "Télécharger {count} par sujet",
    downloadCount: "Télécharger {count}",
    browse: "Voir sur Douyin",
    queueing: "Mise en file…",
    heat: "{value} d'intérêt",
    views: "{value} vues",
    emptyBoard: "Le classement est vide. Réessayez dans quelques instants.",
    topicQueued:
      "{count, plural, one {# vidéo mise en file} other {# vidéos mises en file}} pour « {term} ». Suivez la progression dans Téléchargements.",
    topicFailed: "Impossible de récupérer ce sujet.",
    boardTermsNeedNoAccount:
      "Les sujets du classement ci-dessus n'en demandent pas.",
    connectAccount: "Connecter un compte dans Outils",
  },

  effects: {
    title: "Édition",
    unavailable: "Indisponible sur cette machine",
    licenceRequired: "Une décision de licence est requise avant l'exécution",
    installHint: "Installez le module complémentaire pour l'activer",
  },

  language: {
    switched: "Langue changée : {language}",
  },
};
