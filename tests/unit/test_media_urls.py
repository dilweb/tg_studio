"""Юнит-тесты извлечения медиа-ссылок из ответа AI-агента (бот)."""

from tg_studio.bot.handlers.client_messages import (
    extract_media_urls,
    group_media_by_master,
    strip_urls,
)


def test_extract_portfolio_and_avatar():
    text = (
        "Наши мастера:\n"
        "- Дилявер, классика\n"
        "http://localhost:8000/api/public/masters/5/portfolio/3\n"
        "https://api.example.com/api/public/masters/5/avatar\n"
    )
    assert extract_media_urls(text) == [
        ("http://localhost:8000/api/public/masters/5/portfolio/3", 5, 3),
        ("https://api.example.com/api/public/masters/5/avatar", 5, None),
    ]


def test_extract_dedupes_keeps_first():
    text = (
        "http://x.test/api/public/masters/5/portfolio/3\n"
        "http://x.test/api/public/masters/5/portfolio/3"
    )
    assert extract_media_urls(text) == [
        ("http://x.test/api/public/masters/5/portfolio/3", 5, 3)
    ]


def test_extract_ignores_foreign_urls():
    text = "Смотрите https://instagr.am/p/abc и http://localhost:8000/api/public/masters"
    assert extract_media_urls(text) == []


def test_extract_no_trailing_punctuation():
    text = "Работы: http://x.test/api/public/masters/5/portfolio/7."
    assert extract_media_urls(text) == [
        ("http://x.test/api/public/masters/5/portfolio/7", 5, 7)
    ]


def test_strip_urls_removes_link_lines():
    text = (
        "Наши мастера:\n"
        "Дилявер — классика\n"
        "http://x.test/api/public/masters/5/portfolio/3\n"
        "http://x.test/api/public/masters/5/portfolio/4\n"
        "Что хотите набить?"
    )
    cleaned = strip_urls(text, [
        "http://x.test/api/public/masters/5/portfolio/3",
        "http://x.test/api/public/masters/5/portfolio/4",
    ])
    assert cleaned == "Наши мастера:\nДилявер — классика\nЧто хотите набить?"


def test_strip_urls_keeps_text_when_nothing_sent():
    text = "Работы: http://x.test/api/public/masters/5/portfolio/3"
    assert strip_urls(text, []) == text


def test_strip_urls_empty_result():
    text = "http://x.test/api/public/masters/5/portfolio/3"
    assert strip_urls(text, ["http://x.test/api/public/masters/5/portfolio/3"]) == ""


def test_group_by_master_avatar_and_portfolio():
    media = [
        ("http://x/api/public/masters/5/avatar", 5, None),
        ("http://x/api/public/masters/5/portfolio/3", 5, 3),
        ("http://x/api/public/masters/5/portfolio/4", 5, 4),
    ]
    assert group_media_by_master(media) == [
        (5, "http://x/api/public/masters/5/avatar", [
            ("http://x/api/public/masters/5/portfolio/3", 3),
            ("http://x/api/public/masters/5/portfolio/4", 4),
        ])
    ]


def test_group_two_masters_in_order():
    media = [
        ("http://x/api/public/masters/7/portfolio/1", 7, 1),
        ("http://x/api/public/masters/5/avatar", 5, None),
        ("http://x/api/public/masters/7/portfolio/2", 7, 2),
    ]
    grouped = group_media_by_master(media)
    assert [mid for mid, _, _ in grouped] == [7, 5]
    assert grouped[0] == (7, None, [
        ("http://x/api/public/masters/7/portfolio/1", 1),
        ("http://x/api/public/masters/7/portfolio/2", 2),
    ])
    assert grouped[1] == (5, "http://x/api/public/masters/5/avatar", [])


def test_group_empty():
    assert group_media_by_master([]) == []
