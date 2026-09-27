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
        "press_failed": "That did not go through. Press again, or open the app.",
        "was_approved": "✅ Approved",
        "was_approved_now": "🚀 Approved and posted now",
        "was_dismissed": "🚫 Dismissed",
        "from_a_card": "from a Telegram card, by {who}.",
        "in_the_app": "in the app{who}.",
        "became_posted": "✅ This post has already gone out.",
        "became_not_posted": "⚠️ This post did not go out. The app says why.",
        "became_settled": "This post is no longer waiting for a decision.",
        "more_waiting": "{count} more waiting in the inbox.",
        "test_answer": "This was the test card. Nothing was decided. Pressed by {who}.",
        "notes": "Notes",
        "see_the_app": "see the app",
        "more_overdue": "{count} more overdue, waiting in the inbox.",
        "overdue_notice": "⏰ Still waiting — this was due {when}.",
        "hold_waiting": (
            "Waiting for approval: this exact frozen post reaches its engine only after a person "
            "approves it."
        ),
        "hold_low_pinned": (
            "A product pinned to this post matched its content with low confidence. Approve to "
            "post it anyway, or pin a different one."
        ),
        "hold_low_campaign": (
            "This campaign's one product matched this content with low confidence. Approve to post "
            "it anyway, or change the product in the campaign's settings."
        ),
        "hold_low_smart": (
            "Smart matching found nothing here that fits this post well, so the best available "
            "product is attached. Approve to post it, or pin a product to this post yourself."
        ),
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
        "press_failed": "Chưa thực hiện được. Hãy bấm lại hoặc mở ứng dụng.",
        "was_approved": "✅ Đã duyệt",
        "was_approved_now": "🚀 Đã duyệt và đăng ngay",
        "was_dismissed": "🚫 Đã bỏ qua",
        "from_a_card": "từ thẻ Telegram, bởi {who}.",
        "in_the_app": "trong ứng dụng{who}.",
        "became_posted": "✅ Bài này đã được đăng rồi.",
        "became_not_posted": "⚠️ Bài này chưa đăng được. Hãy xem lý do trong ứng dụng.",
        "became_settled": "Bài này không còn chờ quyết định nữa.",
        "more_waiting": "Còn {count} bài nữa đang chờ trong hộp duyệt.",
        "test_answer": "Đây là thẻ thử. Không có gì được quyết định. {who} đã bấm.",
        "notes": "Ghi chú",
        "see_the_app": "xem trong ứng dụng",
        "more_overdue": "Còn {count} bài quá hạn nữa đang chờ trong hộp duyệt.",
        "overdue_notice": "⏰ Vẫn đang chờ duyệt — bài này đến hạn lúc {when}.",
        "hold_waiting": (
            "Đang chờ duyệt: đúng bài đã chốt này chỉ được gửi đi sau khi có người duyệt."
        ),
        "hold_low_pinned": (
            "Sản phẩm được ghim cho bài này khớp với nội dung ở mức thấp. Duyệt để vẫn đăng, hoặc "
            "ghim một sản phẩm khác."
        ),
        "hold_low_campaign": (
            "Sản phẩm duy nhất của chiến dịch khớp với nội dung này ở mức thấp. Duyệt để vẫn đăng, "
            "hoặc đổi sản phẩm trong cài đặt chiến dịch."
        ),
        "hold_low_smart": (
            "Ghép thông minh không tìm được sản phẩm nào thật sự hợp với bài này, nên sản phẩm khả "
            "dĩ nhất được đính kèm. Duyệt để đăng, hoặc tự ghim một sản phẩm cho bài."
        ),
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
        "press_failed": "実行できませんでした。もう一度押すか、アプリを開いてください。",
        "was_approved": "✅ 承認済み",
        "was_approved_now": "🚀 承認して今すぐ投稿済み",
        "was_dismissed": "🚫 見送り済み",
        "from_a_card": "Telegram のカードから、{who} により。",
        "in_the_app": "アプリで{who}。",
        "became_posted": "✅ この投稿はすでに公開されています。",
        "became_not_posted": "⚠️ この投稿は公開されませんでした。理由はアプリに表示されます。",
        "became_settled": "この投稿はもう決定待ちではありません。",
        "more_waiting": "あと {count} 件が受信箱で待っています。",
        "test_answer": "これはテストカードです。何も決定されていません。{who} が押しました。",
        "notes": "メモ",
        "see_the_app": "アプリを確認してください",
        "more_overdue": "他に{count}件が期限超過で受信箱に待機中です。",
        "overdue_notice": "⏰ まだ承認待ちです — 予定は{when}でした。",
        "hold_waiting": "承認待ち: この確定済みの投稿は、人が承認した後にのみ配信されます。",
        "hold_low_pinned": (
            "この投稿に固定された商品は、内容との一致度が低いものでした。このまま投稿するなら承認、"
            "別の商品を固定することもできます。"
        ),
        "hold_low_campaign": (
            "このキャンペーンの唯一の商品は、この内容との一致度が低いものでした。このまま投稿するな"
            "ら承認、キャンペーン設定で商品を変更することもできます。"
        ),
        "hold_low_smart": (
            "スマートマッチングはこの投稿に本当に合う商品を見つけられず、手元で最も近いものを添付し"
            "ています。承認して投稿するか、自分で商品を固定してください。"
        ),
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
        "press_failed": "Cela n'a pas abouti. Réessayez, ou ouvrez l'application.",
        "was_approved": "✅ Approuvé",
        "was_approved_now": "🚀 Approuvé et publié immédiatement",
        "was_dismissed": "🚫 Écarté",
        "from_a_card": "depuis une carte Telegram, par {who}.",
        "in_the_app": "dans l'application{who}.",
        "became_posted": "✅ Cette publication est déjà partie.",
        "became_not_posted": "⚠️ Cette publication n'est pas partie. L'application dit pourquoi.",
        "became_settled": "Cette publication n'attend plus de décision.",
        "more_waiting": "{count} de plus en attente dans la boîte.",
        "test_answer": "C'était la carte de test. Rien n'a été décidé. Appuyé par {who}.",
        "notes": "Notes",
        "see_the_app": "voir l'app",
        "more_overdue": "{count} de plus en retard, en attente dans la boîte.",
        "overdue_notice": "⏰ Toujours en attente — c'était prévu pour {when}.",
        "hold_waiting": (
            "En attente d'approbation : ce post figé ne part vers son moteur qu'après "
            "l'approbation d'une personne."
        ),
        "hold_low_pinned": (
            "Un produit épinglé à ce post correspond peu à son contenu. Approuvez pour le publier "
            "quand même, ou épinglez-en un autre."
        ),
        "hold_low_campaign": (
            "L'unique produit de cette campagne correspond peu à ce contenu. Approuvez pour le "
            "publier quand même, ou changez le produit dans les réglages de la campagne."
        ),
        "hold_low_smart": (
            "L'association intelligente n'a rien trouvé qui convienne vraiment ici, donc le "
            "meilleur produit disponible est joint. Approuvez pour publier, ou épinglez vous-même "
            "un produit."
        ),
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
        "press_failed": "没有执行成功。请再按一次，或打开应用。",
        "was_approved": "✅ 已批准",
        "was_approved_now": "🚀 已批准并立即发布",
        "was_dismissed": "🚫 已跳过",
        "from_a_card": "来自 Telegram 卡片，由 {who} 操作。",
        "in_the_app": "在应用中{who}。",
        "became_posted": "✅ 这条帖子已经发出。",
        "became_not_posted": "⚠️ 这条帖子没有发出，应用里写明了原因。",
        "became_settled": "这条帖子已不再等待决定。",
        "more_waiting": "收件箱中还有 {count} 条在等待。",
        "test_answer": "这是测试卡片。没有做出任何决定。由 {who} 按下。",
        "notes": "备注",
        "see_the_app": "请查看应用",
        "more_overdue": "还有 {count} 条已超时，正在收件箱中等待。",
        "overdue_notice": "⏰ 仍在等待批准——原定于 {when}。",
        "hold_waiting": "等待批准：这条已冻结的帖子只有在有人批准后才会送往发布引擎。",
        "hold_low_pinned": (
            "固定到这条帖子的商品与内容的匹配度较低。批准则照常发布，或改为固定另一个商品。"
        ),
        "hold_low_campaign": (
            "本活动的唯一商品与此内容的匹配度较低。批准则照常发布，或在活动设置中更换商品。"
        ),
        "hold_low_smart": (
            "智能匹配没有找到真正合适的商品，因此附上了现有最接近的一个。批准即可发布，或自己为这条"
            "帖子固定一个商品。"
        ),
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
        "press_failed": "Не получилось. Нажмите ещё раз или откройте приложение.",
        "was_approved": "✅ Одобрено",
        "was_approved_now": "🚀 Одобрено и опубликовано сразу",
        "was_dismissed": "🚫 Отклонено",
        "from_a_card": "с карточки в Telegram, автор {who}.",
        "in_the_app": "в приложении{who}.",
        "became_posted": "✅ Этот пост уже опубликован.",
        "became_not_posted": "⚠️ Этот пост не был опубликован. Причина указана в приложении.",
        "became_settled": "Этот пост больше не ждёт решения.",
        "more_waiting": "Ещё {count} ждут во входящих.",
        "test_answer": "Это была тестовая карточка. Ничего не решено. Нажал(а): {who}.",
        "notes": "Заметки",
        "see_the_app": "см. приложение",
        "more_overdue": "Ещё {count} просрочены и ждут во входящих.",
        "overdue_notice": "⏰ Всё ещё ждёт — было назначено на {when}.",
        "hold_waiting": (
            "Ожидает одобрения: этот зафиксированный пост уйдёт в публикацию только после того, "
            "как его одобрит человек."
        ),
        "hold_low_pinned": (
            "Товар, закреплённый за этим постом, слабо совпал с его содержанием. Одобрите, чтобы "
            "опубликовать как есть, или закрепите другой."
        ),
        "hold_low_campaign": (
            "Единственный товар этой кампании слабо совпал с этим содержанием. Одобрите, чтобы "
            "опубликовать как есть, или смените товар в настройках кампании."
        ),
        "hold_low_smart": (
            "Умный подбор не нашёл здесь ничего действительно подходящего, поэтому приложен лучший "
            "из доступных. Одобрите публикацию или закрепите товар сами."
        ),
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
        "press_failed": "لم يتم التنفيذ. اضغط مرة أخرى أو افتح التطبيق.",
        "was_approved": "✅ تمت الموافقة",
        "was_approved_now": "🚀 تمت الموافقة والنشر فورًا",
        "was_dismissed": "🚫 تم التخطي",
        "from_a_card": "من بطاقة تيليجرام، بواسطة {who}.",
        "in_the_app": "في التطبيق{who}.",
        "became_posted": "✅ هذا المنشور نُشر بالفعل.",
        "became_not_posted": "⚠️ لم يُنشر هذا المنشور. التطبيق يوضّح السبب.",
        "became_settled": "لم يعد هذا المنشور بانتظار قرار.",
        "more_waiting": "{count} أخرى في انتظارك في صندوق الموافقات.",
        "test_answer": "هذه بطاقة تجريبية. لم يُتّخذ أي قرار. ضغطها {who}.",
        "notes": "ملاحظات",
        "see_the_app": "راجع التطبيق",
        "more_overdue": "{count} أخرى تجاوزت الموعد وتنتظر في صندوق الموافقات.",
        "overdue_notice": "⏰ لا يزال بانتظار الموافقة — كان موعده {when}.",
        "hold_waiting": (
            "في انتظار الموافقة: هذا المنشور المجمَّد لا يصل إلى محرّك النشر إلا بعد موافقة شخص."
        ),
        "hold_low_pinned": (
            "المنتج المثبَّت على هذا المنشور تطابق مع محتواه بثقة منخفضة. وافق لنشره كما هو، أو "
            "ثبّت منتجًا آخر."
        ),
        "hold_low_campaign": (
            "منتج هذه الحملة الوحيد تطابق مع هذا المحتوى بثقة منخفضة. وافق لنشره كما هو، أو غيّر "
            "المنتج في إعدادات الحملة."
        ),
        "hold_low_smart": (
            "لم تجد المطابقة الذكية هنا ما يناسب فعلًا، لذا أُرفق أفضل المتاح. وافق لنشره، أو ثبّت "
            "منتجًا بنفسك."
        ),
    },
}

#: What each network is called, so a card names the place a post is going.
#:
#: Not translated: these are the names the networks call themselves, and an
#: approver looking for the TikTok account is looking for the word TikTok
#: whatever language the rest of the card is in. Level with the interface's
#: own labels, so the card and the app name a destination the same way.
PLATFORMS: dict[str, str] = {
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "facebook": "Facebook",
    "twitter": "X / Twitter",
    "linkedin": "LinkedIn",
    "threads": "Threads",
    "pinterest": "Pinterest",
    "reddit": "Reddit",
    "bluesky": "Bluesky",
    "mastodon": "Mastodon",
    "telegram": "Telegram",
    "googlebusiness": "Google Business",
    "douyin": "Douyin",
}


def platform_name(platform: str | None) -> str:
    """A network's own name, or the id itself for one nothing has named yet.

    An unknown id is shown rather than swallowed: a card that silently drops
    where a post is going is worse than one that says `bluesky2` while
    somebody adds the proper name here.
    """
    if not platform:
        return ""
    return PLATFORMS.get(platform, platform)


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
