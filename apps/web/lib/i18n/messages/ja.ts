import type { Messages } from "./en";

/** Japanese. Noun-style labels for navigation and する-verbs for actions, which
 *  is how Japanese product interfaces read; polite but not honorific-heavy. */
export const ja: Messages = {
  app: {
    name: "TrendRelay",
    tagline: "トレンドを見つけ、価値ある情報を調べる。",
  },

  nav: {
    discover: "発見",
    library: "ライブラリ",
    studio: "スタジオ",
    campaigns: "キャンペーン",
    publish: "投稿",
    catalog: "カタログ",
    attribution: "効果測定",
    opportunities: "商機",
    tools: "ツール",
    workspaces: "ワークスペース",
    about: "このアプリについて",
    signIn: "ログイン",
    signOut: "ログアウト",
    language: "言語",
    chooseLanguage: "言語を選択",
  },

  common: {
    save: "保存",
    cancel: "キャンセル",
    close: "閉じる",
    delete: "削除",
    confirm: "確認",
    retry: "再試行",
    reload: "再読み込み",
    refresh: "更新",
    loading: "読み込み中…",
    search: "検索",
    filter: "絞り込み",
    clear: "クリア",
    selectAll: "すべて選択",
    none: "なし",
    all: "すべて",
    open: "開く",
    download: "ダウンロード",
    preview: "プレビュー",
    apply: "適用",
    back: "戻る",
    next: "次へ",
    yes: "はい",
    no: "いいえ",
    optional: "任意",
    required: "必須",
    unavailable: "利用できません",
    comingSoon: "近日公開",
  },

  status: {
    queued: "待機中",
    running: "実行中",
    succeeded: "完了",
    failed: "失敗",
    partial: "一部完了",
    cancelled: "キャンセル済み",
    ready: "準備完了",
    setupRequired: "設定が必要",
  },

  workspace: {
    loading: "ワークスペースを読み込み中…",
    loadingHelp:
      "通常はすぐに終わります。終わらない場合は API が起動していない可能性があります。",
    none: "ワークスペースがありません",
    select: "ワークスペース",
  },

  discover: {
    title: "Douyin 急上昇ワード",
    subtitleEmpty:
      "接続中のセッションから見た、Douyin でいま話題になっているもの。",
    read: "ランキングを取得",
    reading: "取得中…",
    gallery: "グリッド表示",
    list: "リスト表示",
    boardLayout: "表示形式",
    downloadPerTopic: "1 トピックにつき {count} 件",
    downloadCount: "{count} 件ダウンロード",
    browse: "Douyin で見る",
    queueing: "登録中…",
    heat: "話題度 {value}",
    views: "{value} 回視聴",
    emptyBoard: "ランキングが空でした。しばらくしてからお試しください。",
    topicQueued:
      "「{term}」の動画を {count, plural, other {# 件}} 登録しました。ダウンロードで進行状況を確認できます。",
    topicFailed: "このトピックを取得できませんでした。",
    boardTermsNeedNoAccount: "上のランキングのワードにはアカウントは不要です。",
    connectAccount: "ツールでアカウントを接続",
  },

  effects: {
    title: "編集",
    unavailable: "この環境では利用できません",
    licenceRequired: "実行前にライセンスの確認が必要です",
    installHint: "アドオンをインストールすると利用できます",
  },

  language: {
    switched: "言語を{language}に変更しました",
  },
};
