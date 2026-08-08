import type { Messages } from "./en";

/** Simplified Chinese. Short verb-object labels, which is what Chinese product
 *  interfaces use — "发现" not "去发现", "保存" not "进行保存". */
export const zh: Messages = {
  app: {
    name: "TrendRelay",
    tagline: "发现趋势，研究真正重要的内容。",
  },

  nav: {
    discover: "发现",
    library: "素材库",
    studio: "创作台",
    campaigns: "广告系列",
    publish: "发布",
    catalog: "商品库",
    attribution: "归因分析",
    opportunities: "商机",
    tools: "工具",
    workspaces: "工作区",
    about: "关于",
    signIn: "登录",
    signOut: "退出登录",
    language: "语言",
    chooseLanguage: "选择语言",
  },

  common: {
    save: "保存",
    cancel: "取消",
    close: "关闭",
    delete: "删除",
    confirm: "确认",
    retry: "重试",
    reload: "重新加载",
    refresh: "刷新",
    loading: "加载中…",
    search: "搜索",
    filter: "筛选",
    clear: "清除",
    selectAll: "全选",
    none: "无",
    all: "全部",
    open: "打开",
    download: "下载",
    preview: "预览",
    apply: "应用",
    back: "返回",
    next: "下一步",
    yes: "是",
    no: "否",
    optional: "可选",
    required: "必填",
    unavailable: "不可用",
    comingSoon: "即将推出",
  },

  status: {
    queued: "排队中",
    running: "进行中",
    succeeded: "已完成",
    failed: "失败",
    partial: "部分完成",
    cancelled: "已取消",
    ready: "就绪",
    setupRequired: "需要配置",
  },

  workspace: {
    loading: "正在加载工作区…",
    loadingHelp: "通常很快就好。如果一直加载，可能是 API 没有启动。",
    none: "暂无工作区",
    select: "工作区",
  },

  discover: {
    title: "抖音热搜",
    subtitleEmpty: "来自你已连接会话的抖音实时热门内容。",
    read: "获取热搜榜",
    reading: "获取中…",
    gallery: "网格视图",
    list: "列表视图",
    boardLayout: "显示方式",
    downloadPerTopic: "每个话题下载 {count} 个",
    downloadCount: "下载 {count} 个",
    browse: "在抖音查看",
    queueing: "正在加入队列…",
    heat: "热度 {value}",
    views: "{value} 次播放",
    emptyBoard: "热搜榜暂时为空，请稍后再试。",
    topicQueued:
      "已将“{term}”的 {count, plural, other {# 个视频}}加入队列，可在下载中查看进度。",
    topicFailed: "无法获取该话题。",
    boardTermsNeedNoAccount: "上面榜单里的话题不需要账号。",
    connectAccount: "在工具中连接账号",
  },

  effects: {
    title: "编辑",
    unavailable: "当前设备不可用",
    licenceRequired: "运行前需要先确认许可条款",
    installHint: "安装扩展组件后即可使用",
  },

  language: {
    switched: "已切换为{language}",
  },
};
