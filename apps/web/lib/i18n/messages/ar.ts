import type { Messages } from "./en";

/** Modern Standard Arabic, right-to-left. Verbal nouns (المصدر) for actions and
 *  plain nouns for sections, which is how Arabic interfaces read — "حفظ", not
 *  "احفظ". Arabic has six plural categories; the dictionary supplies the ones
 *  these counts actually reach and Intl.PluralRules picks between them. */
export const ar: Messages = {
  app: {
    name: "TrendRelay",
    tagline: "اكتشف الرائج. وابحث فيما يهم.",
  },

  nav: {
    discover: "استكشاف",
    library: "المكتبة",
    studio: "الاستوديو",
    campaigns: "الحملات",
    publish: "النشر",
    catalog: "كتالوج المنتجات",
    attribution: "إسناد النتائج",
    opportunities: "الفرص",
    tools: "الأدوات",
    workspaces: "مساحات العمل",
    about: "حول التطبيق",
    signIn: "تسجيل الدخول",
    signOut: "تسجيل الخروج",
    language: "اللغة",
    chooseLanguage: "اختر اللغة",
  },

  common: {
    save: "حفظ",
    cancel: "إلغاء",
    close: "إغلاق",
    delete: "حذف",
    confirm: "تأكيد",
    retry: "إعادة المحاولة",
    reload: "إعادة التحميل",
    refresh: "تحديث",
    loading: "جارٍ التحميل…",
    search: "بحث",
    filter: "تصفية",
    clear: "مسح",
    selectAll: "تحديد الكل",
    none: "لا شيء",
    all: "الكل",
    open: "فتح",
    download: "تنزيل",
    preview: "معاينة",
    apply: "تطبيق",
    back: "رجوع",
    next: "التالي",
    yes: "نعم",
    no: "لا",
    optional: "اختياري",
    required: "مطلوب",
    unavailable: "غير متاح",
    comingSoon: "قريبًا",
  },

  status: {
    queued: "في الانتظار",
    running: "قيد التنفيذ",
    succeeded: "اكتمل",
    failed: "فشل",
    partial: "اكتمل جزئيًا",
    cancelled: "أُلغي",
    ready: "جاهز",
    setupRequired: "يتطلب إعدادًا",
  },

  workspace: {
    loading: "جارٍ تحميل مساحة العمل…",
    loadingHelp:
      "يستغرق هذا لحظة عادةً. إن طال الأمر، فقد لا تكون واجهة API قيد التشغيل.",
    none: "لا توجد مساحة عمل بعد",
    select: "مساحة العمل",
  },

  discover: {
    title: "الأكثر بحثًا على Douyin",
    subtitleEmpty: "ما هو رائج الآن على Douyin، من جلستك المتصلة.",
    read: "تحميل القائمة",
    reading: "جارٍ التحميل…",
    gallery: "عرض شبكي",
    list: "عرض قائمة",
    boardLayout: "طريقة العرض",
    downloadPerTopic: "تنزيل {count} لكل موضوع",
    downloadCount: "تنزيل {count}",
    browse: "العرض على Douyin",
    queueing: "جارٍ الإضافة إلى قائمة الانتظار…",
    heat: "درجة الرواج {value}",
    views: "{value} مشاهدة",
    emptyBoard: "القائمة فارغة حاليًا. حاول مرة أخرى بعد قليل.",
    topicQueued:
      "تمت إضافة {count, plural, one {مقطع واحد} two {مقطعين} few {# مقاطع} many {# مقطعًا} other {# مقطع}} عن «{term}» إلى قائمة الانتظار. تابع التقدم في التنزيلات.",
    topicFailed: "تعذّر جلب هذا الموضوع.",
    boardTermsNeedNoAccount: "أما مواضيع القائمة أعلاه فلا تحتاج إليه.",
    connectAccount: "اربط حسابًا من الأدوات",
  },

  effects: {
    title: "التحرير",
    unavailable: "غير متاح على هذا الجهاز",
    licenceRequired: "يلزم قرار بشأن الترخيص قبل التشغيل",
    installHint: "ثبّت الإضافة لتفعيل هذه الميزة",
  },

  language: {
    switched: "تم تغيير اللغة إلى {language}",
  },
};
