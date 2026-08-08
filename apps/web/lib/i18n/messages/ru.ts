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
