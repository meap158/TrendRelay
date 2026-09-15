"""What a Telegram approval card says, in the languages the interface speaks.

A card is read on a phone by whoever approves a campaign's posts, and a
Vietnamese campaign's approver reads Vietnamese. The words here are the
card's own - its buttons, what it says once decided, why a press was refused
- and are looked up in the campaign's language, with English behind any gap.
The same seven languages the interface has, so a campaign cannot ask for a
language its cards could not be written in.
"""

from __future__ import annotations

from datetime import datetime

#: The card's strings by language. English is the reference; every other
#: language is checked against it by a test, so a key added to one is added
#: to all before it can ship.
WORDS: dict[str, dict[str, str]] = {
    "en": {
        "approve": "✅ Approve",
        "dismiss": "🚫 Dismiss",
        "approve_now": "🚀 Approve and post now",
        "open_app": "↗ Open in app",
        "approved_by": "✅ Approved by {who}",
        "approved_now_by": "🚀 Approved and posting now by {who}",
        "dismissed_by": "🚫 Dismissed by {who}",
        "approved_but_failed": "⚠️ Approved by {who}, but it could not be queued: {reason}",
        "already_decided": "Already decided in the app: it is {state}.",
        "not_this_chat": "This chat is not the one TrendRelay was set up with.",
        "not_approver": "You are not on the approvers list for this workspace.",
        "not_ours": "That button is not one of ours.",
        "gone": "That post is no longer here.",
        "no_autopilot": "That campaign no longer runs on its own.",
        "more_waiting": "{count} more waiting in the inbox.",
        "test_answer": "This was the test card. Nothing was decided. Pressed by {who}.",
        "notes": "Notes",
        "see_the_app": "see the app",
    },
    "vi": {
        "approve": "✅ Duyệt",
        "dismiss": "🚫 Bỏ qua",
        "approve_now": "🚀 Duyệt và đăng ngay",
        "open_app": "↗ Mở trong ứng dụng",
        "approved_by": "✅ {who} đã duyệt",
        "approved_now_by": "🚀 {who} đã duyệt, đang đăng ngay",
        "dismissed_by": "🚫 {who} đã bỏ qua",
        "approved_but_failed": "⚠️ {who} đã duyệt, nhưng không xếp hàng được: {reason}",
        "already_decided": "Đã được quyết định trong ứng dụng: trạng thái {state}.",
        "not_this_chat": "Đây không phải nhóm chat đã cài đặt cho TrendRelay.",
        "not_approver": "Bạn không có trong danh sách người duyệt của không gian làm việc này.",
        "not_ours": "Nút này không phải của chúng tôi.",
        "gone": "Bài đăng này không còn ở đây nữa.",
        "no_autopilot": "Chiến dịch này không còn tự chạy nữa.",
        "more_waiting": "Còn {count} bài nữa đang chờ trong hộp duyệt.",
        "test_answer": "Đây là thẻ thử. Không có gì được quyết định. {who} đã bấm.",
        "notes": "Ghi chú",
        "see_the_app": "xem trong ứng dụng",
    },
    "ja": {
        "approve": "✅ 承認",
        "dismiss": "🚫 見送る",
        "approve_now": "🚀 承認して今すぐ投稿",
        "open_app": "↗ アプリで開く",
        "approved_by": "✅ {who} が承認しました",
        "approved_now_by": "🚀 {who} が承認し、今すぐ投稿します",
        "dismissed_by": "🚫 {who} が見送りました",
        "approved_but_failed": "⚠️ {who} が承認しましたが、キューに入れられませんでした: {reason}",
        "already_decided": "アプリで既に決定済みです: 状態は {state} です。",
        "not_this_chat": "このチャットは TrendRelay に設定されたものではありません。",
        "not_approver": "このワークスペースの承認者リストに含まれていません。",
        "not_ours": "このボタンは私たちのものではありません。",
        "gone": "この投稿はもうここにありません。",
        "no_autopilot": "このキャンペーンはもう自動で動いていません。",
        "more_waiting": "あと {count} 件が受信箱で待っています。",
        "test_answer": "これはテストカードです。何も決定されていません。{who} が押しました。",
        "notes": "メモ",
        "see_the_app": "アプリを確認してください",
    },
    "fr": {
        "approve": "✅ Approuver",
        "dismiss": "🚫 Écarter",
        "approve_now": "🚀 Approuver et publier maintenant",
        "open_app": "↗ Ouvrir dans l'app",
        "approved_by": "✅ Approuvé par {who}",
        "approved_now_by": "🚀 Approuvé par {who}, publication en cours",
        "dismissed_by": "🚫 Écarté par {who}",
        "approved_but_failed": "⚠️ Approuvé par {who}, mais impossible de le mettre en file : {reason}",
        "already_decided": "Déjà décidé dans l'app : état {state}.",
        "not_this_chat": "Cette conversation n'est pas celle configurée pour TrendRelay.",
        "not_approver": "Vous n'êtes pas dans la liste des approbateurs de cet espace.",
        "not_ours": "Ce bouton n'est pas l'un des nôtres.",
        "gone": "Cette publication n'est plus ici.",
        "no_autopilot": "Cette campagne ne tourne plus toute seule.",
        "more_waiting": "{count} de plus en attente dans la boîte.",
        "test_answer": "C'était la carte de test. Rien n'a été décidé. Appuyé par {who}.",
        "notes": "Notes",
        "see_the_app": "voir l'app",
    },
    "zh": {
        "approve": "✅ 批准",
        "dismiss": "🚫 不发",
        "approve_now": "🚀 批准并立即发布",
        "open_app": "↗ 在应用中打开",
        "approved_by": "✅ {who} 已批准",
        "approved_now_by": "🚀 {who} 已批准，正在立即发布",
        "dismissed_by": "🚫 {who} 已选择不发",
        "approved_but_failed": "⚠️ {who} 已批准，但无法加入队列：{reason}",
        "already_decided": "已在应用中决定：当前状态为 {state}。",
        "not_this_chat": "这个聊天不是为 TrendRelay 设置的那个。",
        "not_approver": "你不在这个工作区的审批人名单中。",
        "not_ours": "这个按钮不是我们的。",
        "gone": "这条帖子已不在这里。",
        "no_autopilot": "这个活动已不再自动运行。",
        "more_waiting": "收件箱中还有 {count} 条在等待。",
        "test_answer": "这是测试卡片。没有做出任何决定。由 {who} 按下。",
        "notes": "备注",
        "see_the_app": "请查看应用",
    },
    "ru": {
        "approve": "✅ Одобрить",
        "dismiss": "🚫 Отклонить",
        "approve_now": "🚀 Одобрить и опубликовать сейчас",
        "open_app": "↗ Открыть в приложении",
        "approved_by": "✅ Одобрено: {who}",
        "approved_now_by": "🚀 Одобрено и публикуется сейчас: {who}",
        "dismissed_by": "🚫 Отклонено: {who}",
        "approved_but_failed": "⚠️ Одобрено ({who}), но поставить в очередь не удалось: {reason}",
        "already_decided": "Уже решено в приложении: состояние {state}.",
        "not_this_chat": "Этот чат не тот, для которого настроен TrendRelay.",
        "not_approver": "Вас нет в списке утверждающих этого рабочего пространства.",
        "not_ours": "Эта кнопка не наша.",
        "gone": "Этого поста здесь больше нет.",
        "no_autopilot": "Эта кампания больше не работает сама.",
        "more_waiting": "Ещё {count} ждут во входящих.",
        "test_answer": "Это была тестовая карточка. Ничего не решено. Нажал(а): {who}.",
        "notes": "Заметки",
        "see_the_app": "см. приложение",
    },
    "ar": {
        "approve": "✅ موافقة",
        "dismiss": "🚫 تجاهل",
        "approve_now": "🚀 موافقة ونشر الآن",
        "open_app": "↗ فتح في التطبيق",
        "approved_by": "✅ وافق عليه {who}",
        "approved_now_by": "🚀 وافق عليه {who} ويُنشر الآن",
        "dismissed_by": "🚫 تجاهله {who}",
        "approved_but_failed": "⚠️ وافق عليه {who} لكن تعذّر وضعه في الطابور: {reason}",
        "already_decided": "تم البتّ فيه في التطبيق مسبقًا: حالته {state}.",
        "not_this_chat": "هذه المحادثة ليست التي أُعدّ عليها TrendRelay.",
        "not_approver": "لست ضمن قائمة المعتمدين لمساحة العمل هذه.",
        "not_ours": "هذا الزر ليس من أزرارنا.",
        "gone": "هذا المنشور لم يعد هنا.",
        "no_autopilot": "هذه الحملة لم تعد تعمل تلقائيًا.",
        "more_waiting": "{count} أخرى في انتظارك في صندوق الموافقات.",
        "test_answer": "هذه بطاقة تجريبية. لم يُتّخذ أي قرار. ضغطها {who}.",
        "notes": "ملاحظات",
        "see_the_app": "راجع التطبيق",
    },
}

#: Short weekday names, so a due time reads in the card's language rather
#: than in the server's locale. Monday first, as `datetime.weekday` counts.
WEEKDAYS: dict[str, tuple[str, ...]] = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "vi": ("T2", "T3", "T4", "T5", "T6", "T7", "CN"),
    "ja": ("月", "火", "水", "木", "金", "土", "日"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
    "zh": ("周一", "周二", "周三", "周四", "周五", "周六", "周日"),
    "ru": ("пн", "вт", "ср", "чт", "пт", "сб", "вс"),
    "ar": ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"),
}

#: Short month names, for the same reason. January first.
MONTHS: dict[str, tuple[str, ...]] = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
    "ru": ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"),
}


def known(language: str | None) -> bool:
    return bool(language) and language in WORDS


def language_for(*candidates: str | None) -> str:
    """The first candidate the cards can be written in, else English."""
    for candidate in candidates:
        if known(candidate):
            return str(candidate)
    return "en"


def say(language: str, key: str, **values: object) -> str:
    """One string in the card's language, English behind any gap."""
    table = WORDS.get(language) or WORDS["en"]
    text = table.get(key) or WORDS["en"][key]
    return text.format(**values) if values else text


def when(at: datetime, language: str) -> str:
    """A due time as the card's reader writes dates.

    Weekday and time in every language; the date itself day-first with a
    named month where the language has short month names, and numeric
    elsewhere - a Vietnamese or Japanese reader writes 16/09, not 16 Sep.
    """
    weekday = WEEKDAYS.get(language, WEEKDAYS["en"])[at.weekday()]
    clock = at.strftime("%H:%M")
    months = MONTHS.get(language)
    if months:
        return f"{weekday} {at.day:02d} {months[at.month - 1]}, {clock}"
    if language == "ja" or language == "zh":
        return f"{at.month}月{at.day}日({weekday}) {clock}"
    return f"{weekday} {at.day:02d}/{at.month:02d}, {clock}"
