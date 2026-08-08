import type { Messages } from "./en";

/** Russian. Infinitives for buttons and nominative nouns for sections, which is
 *  the standard register for Russian interfaces. Plurals use the one/few/many
 *  categories Intl.PluralRules selects. */
export const ru: Messages = {
  app: {
    name: "TrendRelay",
    tagline: "Находите тренды. Изучайте то, что важно.",
  },

  nav: {
    discover: "Обзор трендов",
    library: "Библиотека",
    studio: "Студия",
    campaigns: "Кампании",
    publish: "Публикация",
    catalog: "Каталог",
    attribution: "Атрибуция",
    opportunities: "Возможности",
    tools: "Инструменты",
    workspaces: "Рабочие пространства",
    about: "О программе",
    signIn: "Войти",
    signOut: "Выйти",
    language: "Язык",
    chooseLanguage: "Выберите язык",
  },

  common: {
    save: "Сохранить",
    cancel: "Отмена",
    close: "Закрыть",
    delete: "Удалить",
    confirm: "Подтвердить",
    retry: "Повторить",
    reload: "Перезагрузить",
    refresh: "Обновить",
    loading: "Загрузка…",
    search: "Поиск",
    filter: "Фильтр",
    clear: "Очистить",
    selectAll: "Выбрать все",
    none: "Нет",
    all: "Все",
    open: "Открыть",
    download: "Скачать",
    preview: "Предпросмотр",
    apply: "Применить",
    back: "Назад",
    next: "Далее",
    yes: "Да",
    no: "Нет",
    optional: "Необязательно",
    required: "Обязательно",
    unavailable: "Недоступно",
    comingSoon: "Скоро",
  },

  status: {
    queued: "В очереди",
    running: "Выполняется",
    succeeded: "Готово",
    failed: "Ошибка",
    partial: "Выполнено частично",
    cancelled: "Отменено",
    ready: "Готов",
    setupRequired: "Требуется настройка",
  },

  workspace: {
    loading: "Загрузка рабочего пространства…",
    loadingHelp:
      "Обычно это занимает мгновение. Если нет — возможно, API не запущен.",
    none: "Рабочих пространств пока нет",
    select: "Рабочее пространство",
  },

  discover: {
    title: "Популярное в Douyin",
    subtitleEmpty:
      "Что сейчас в тренде на Douyin — по данным вашей подключённой сессии.",
    read: "Загрузить подборку",
    reading: "Загрузка…",
    gallery: "Плитка",
    list: "Список",
    boardLayout: "Вид",
    downloadPerTopic: "Скачивать по {count} на тему",
    downloadCount: "Скачать {count}",
    browse: "Открыть в Douyin",
    queueing: "Добавление в очередь…",
    heat: "Популярность: {value}",
    views: "{value} просмотров",
    emptyBoard: "Подборка пуста. Попробуйте ещё раз чуть позже.",
    topicQueued:
      "{count, plural, one {# видео добавлено} few {# видео добавлено} many {# видео добавлено} other {# видео добавлено}} по запросу «{term}». Следите за ходом в разделе «Загрузки».",
    topicFailed: "Не удалось получить эту тему.",
    boardTermsNeedNoAccount: "Для тем из подборки выше он не нужен.",
    connectAccount: "Подключить аккаунт в разделе «Инструменты»",
  },

  downloads: {
    heading: "Загрузка из Douyin",
    eyebrowAcquisition: "СБОР МАТЕРИАЛА",
    intro:
      "Вставьте ссылки на видео, профили или подборки. TrendRelay загрузит их в фоне и добавит файлы в вашу библиотеку.",
    signInPrompt: "Войдите, чтобы управлять материалами.",
    signInIntro:
      "Загружайте исходные видео, готовьте нарезки и публикуйте одобренные посты — всё в одном рабочем пространстве.",
    tryAgain: "Попробовать снова",
    workspaceFirst: "Сначала создайте рабочее пространство",
    workspaceOwns:
      "Рабочее пространство хранит материалы, согласования и историю публикаций.",
    createWorkspace: "Создать рабочее пространство",
    step: "ШАГ {number}",
    addLinks: "Добавить ссылки Douyin",
    addLinksHelp:
      "Вставьте скопированное сообщение с ссылкой или укажите по одной ссылке в строке.",
    linksLabel: "Ссылки Douyin",
    pasteFromClipboard: "Вставить из буфера обмена",
    readyToDownload: "Готово к загрузке",
    remove: "Убрать",
    installProvider: "Установить загрузчик Douyin",
    installProviderHelp:
      "Включите управляемый компонент один раз и вернитесь сюда.",
    openTools: "Открыть инструменты",
    refreshSession: "Обновить сессию Douyin",
    refreshSessionHelp:
      "Сессия Douyin хранится локально. Обновляйте её, только если загрузки перестали работать.",
    options: "Параметры загрузки",
    fromProfiles: "Материалы из профилей",
    publishedPosts: "Опубликованные записи",
    likedVideos: "Понравившиеся видео",
    collections: "Подборки",
    musicVideos: "Видео с музыкой",
    perSource: "Видео на источник",
    whatToFetch: "Что загружать",
    whatToFetchHelp:
      "По умолчанию — все видео. TrendRelay постранично обходит источник и пропускает уже загруженные файлы. Снятый флажок означает, что материал вообще не запрашивается, а не загружается и выбрасывается.",
    authorisedOnly:
      "Загружайте только те материалы, которые вы вправе хранить и использовать повторно.",
    heading2: "Загрузки",
    autoUpdate: "Активные пакеты обновляются каждые несколько секунд.",
    openSource: "Открыть источник",
    openFolder: "Открыть папку",
    openLibrary: "Открыть библиотеку",
    noneSaved: "Ни один файл не сохранён",
    reuseLinks: "Обновите сессию Douyin и используйте эти ссылки повторно.",
    openInLibrary: "Открыть в библиотеке",
    plan: "Спланировать",
    detectedSources: "Найденные источники Douyin",
    refreshList: "Обновить загрузки",
    filter: "Фильтровать загрузки",
    addCreatorProfile:
      "Добавьте профиль автора в поле ссылок, чтобы загрузить весь его каталог",
  },

  research: {
    results: "Результаты",
    relevance: "Релевантность",
    noSignals: "Сигналов этого типа пока нет.",
    recent: "Недавние исследования",
    noImage: "без изображения",
    tiktokRegion: "Регион TikTok",
    tiktokPeriod: "Период TikTok",
    perTopicHelp: "Сколько видео загружать на одну тему",
    openTermOnDouyin: "Открыть запрос в Douyin",
  },

  effects: {
    title: "Редактирование",
    unavailable: "Недоступно на этом компьютере",
    licenceRequired: "Перед запуском нужно решение по лицензии",
    installHint: "Установите дополнение, чтобы включить эту функцию",
  },

  language: {
    switched: "Язык изменён на {language}",
  },
};
