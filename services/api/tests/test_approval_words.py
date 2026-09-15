"""The Telegram card speaks the campaign's language.

Every language the interface has gets every string the card can say, checked
against English so a key added to one is added to all before it ships.
"""

from __future__ import annotations

from datetime import UTC, datetime

from trendrelay_api import approval_words as words
from trendrelay_api.campaign_autopilot import LOCALISED_TEXTS


def test_every_interface_language_has_every_card_string() -> None:
    reference = set(words.WORDS["en"])
    # Level with the scaffolding's languages, which are the interface's.
    assert set(words.WORDS) == set(LOCALISED_TEXTS)
    for language, table in words.WORDS.items():
        assert set(table) == reference, language
        for key, text in table.items():
            # Every placeholder the English carries is carried, so a card
            # never loses the name of who decided it in translation.
            for slot in ("{who}", "{state}", "{count}", "{reason}"):
                assert (slot in text) == (slot in words.WORDS["en"][key]), (language, key)
        assert set(words.WEEKDAYS[language]) and len(words.WEEKDAYS[language]) == 7


def test_a_string_falls_back_to_english_only_where_there_is_a_gap(monkeypatch) -> None:
    assert words.say("vi", "approve") == "✅ Duyệt"
    assert words.say("vi", "approved_by", who="@ana") == "✅ @ana đã duyệt"
    monkeypatch.setitem(words.WORDS["vi"], "approve", "")
    assert words.say("vi", "approve") == "✅ Approve"
    assert words.say("xx", "dismiss") == "🚫 Dismiss"


def test_the_language_is_the_choice_then_the_campaign_s_then_english() -> None:
    assert words.language_for("ja", "vi") == "ja"
    assert words.language_for(None, "vi") == "vi"
    assert words.language_for("", "xx") == "en"
    assert words.known("ar") and not words.known("xx") and not words.known(None)


def test_a_due_time_reads_as_the_reader_writes_dates() -> None:
    at = datetime(2026, 8, 10, 19, 0, tzinfo=UTC)  # a Monday
    assert words.when(at, "en") == "Mon 10 Aug, 19:00"
    assert words.when(at, "fr") == "lun. 10 août, 19:00"
    assert words.when(at, "vi") == "T2 10/08, 19:00"
    assert words.when(at, "ja") == "8月10日(月) 19:00"
    assert words.when(at, "zh") == "8月10日(周一) 19:00"
    assert words.when(at, "ru") == "пн 10 авг, 19:00"
    assert words.when(at, "ar") == "الاثنين 10/08, 19:00"
