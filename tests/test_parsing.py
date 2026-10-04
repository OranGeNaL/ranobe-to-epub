"""Тесты разбора данных книги на реальных фикстурах API (задачи 4.1-4.6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ranobelib_epub.models import Book, Cover
from ranobelib_epub.source.parsing import (
    parse_author,
    parse_book,
    parse_branches,
    parse_chapter_content,
    parse_chapters,
    parse_cover,
    parse_covers,
    parse_status,
    summary_text,
)

FIXTURES = Path(__file__).parent / "fixtures"
SLUG = "94231--rezero-kara-hajimeru-isekai-seikatsu-outo-no-ichinichi-hen"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def book_payload() -> Any:
    return load("book.json")


@pytest.fixture
def chapters_payload() -> Any:
    return load("chapters.json")


class TestParseBook:
    def test_metadata(self, book_payload: Any) -> None:
        book = parse_book(book_payload)

        assert isinstance(book, Book)
        assert book.book_id == 94231
        assert book.slug_url == SLUG
        assert book.rus_name == "Re:Zero. Жизнь с нуля в альтернативном мире (Веб-новелла)"
        assert book.name == "Re:Zero kara Hajimeru Isekai Seikatsu Outo no Ichinichi Hen (WN)"
        assert book.status == "Онгоинг"
        assert book.release_date == "2012 г."
        assert book.year == 2012

    def test_cover_url(self, book_payload: Any) -> None:
        cover = parse_book(book_payload).cover

        assert cover is not None
        assert cover.startswith("https://")
        assert cover.endswith(".jpg")

    def test_age_restriction(self, book_payload: Any) -> None:
        book = parse_book(book_payload)

        assert book.age_restriction_id == 4
        assert book.age_restriction_label == "18+"

    def test_missing_optional_fields_become_none(self) -> None:
        book = parse_book({"data": {"id": 1, "slug_url": "1--x"}})

        assert book.cover is None
        assert book.rus_name is None
        assert book.status is None
        assert book.year is None
        assert book.genres == ()
        assert book.in_language is None
        assert book.age_restriction_id is None

    def test_cover_object_variants(self) -> None:
        assert parse_cover({"default": "https://x/a.jpg", "thumbnail": "https://x/t.jpg"}) == (
            "https://x/a.jpg"
        )
        assert parse_cover({"thumbnail": "https://x/t.jpg"}) == "https://x/t.jpg"
        assert parse_cover(None) is None

    def test_status_object_variants(self) -> None:
        assert parse_status({"id": 1, "label": "Онгоинг"}) == "Онгоинг"
        assert parse_status("Завершена") == "Завершена"
        assert parse_status(None) is None

    def test_author_is_read_from_expanded_fields(self, book_payload: Any) -> None:
        book = parse_book(book_payload)

        assert book.author == "Tappei Nagatsuki"

    def test_genres_from_expanded_fields(self, book_payload: Any) -> None:
        book = parse_book(book_payload)

        assert book.genres == ("Героическое фэнтези", "Драма")

    def test_summary_becomes_plain_text(self, book_payload: Any) -> None:
        book = parse_book(book_payload)

        assert book.summary is not None
        assert book.summary.startswith("Возвращаясь домой из магазина")
        assert "Юноша ждёт встречи" in book.summary
        assert "\n" not in book.summary

    def test_missing_author_falls_back_to_plain_field(self) -> None:
        book = parse_book(
            {"data": {"id": 1, "slug_url": "1--x", "author": "Классик", "authors": None}}
        )

        assert book.author == "Классик"


class TestParseAuthor:
    def test_single_author(self) -> None:
        assert parse_author([{"name": "Tappei Nagatsuki"}]) == "Tappei Nagatsuki"

    def test_multiple_authors_joined(self) -> None:
        raw = [{"name": "Первый"}, {"name": None, "rus_name": "Второй"}]

        assert parse_author(raw) == "Первый, Второй"

    def test_rus_name_fallback(self) -> None:
        assert parse_author([{"name": None, "rus_name": "Русское имя"}]) == "Русское имя"

    def test_empty_and_missing(self) -> None:
        assert parse_author(None) is None
        assert parse_author([]) is None
        assert parse_author([{"name": None, "rus_name": None}]) is None


class TestSummaryText:
    def test_prosemirror_document_becomes_text(self) -> None:
        raw = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Первая часть"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Вторая часть"}]},
            ],
        }

        assert summary_text(raw) == "Первая часть Вторая часть"

    def test_plain_string_passes_through(self) -> None:
        assert summary_text("Описание") == "Описание"

    def test_whitespace_variants_collapse(self) -> None:
        raw = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "a   b"}]},
                {"type": "text", "text": " c "},
            ],
        }

        assert summary_text(raw) == "a b c"

    def test_empty_document_is_none(self) -> None:
        assert summary_text(None) is None
        assert summary_text({"type": "doc", "content": []}) is None
        assert summary_text({"type": "paragraph", "content": []}) is None


class TestParseCovers:
    def test_all_covers_sorted_by_order(self) -> None:
        covers = parse_covers(load("covers.json"))

        assert isinstance(covers, tuple)
        assert [cover.order for cover in covers] == [0, 1, 2, 3]
        assert all(isinstance(cover, Cover) for cover in covers)

    def test_label_and_url_priority(self) -> None:
        covers = parse_covers(load("covers.json"))

        assert covers[0].label == "Re:Zero — Том 1"
        assert covers[0].url.endswith("_orig.jpg"), "приоритет отдаётся orig"
        assert covers[1].label == "Том 2", "info отсутствует — метка из порядка"
        assert covers[1].url.endswith("cover_1.jpg"), "orig нет — берётся default"
        assert covers[2].url.endswith("_thumb.jpg"), "остаётся только thumbnail"
        assert covers[3].url is None, "обложка без вариантов не ломает сборку"

    def test_empty_and_broken_responses(self) -> None:
        assert parse_covers({"data": []}) == ()
        assert parse_covers({"data": None}) == ()
        assert parse_covers("не список") == ()
        assert parse_covers({"data": [{"order": 0}]}) == (), "без id запись пропускается"

    def test_numeric_info_formats_volume_label(self) -> None:
        covers = parse_covers(
            {
                "data": [
                    {"id": 1, "order": 0, "info": "1", "cover": {"thumbnail": "https://c/a.jpg"}},
                    {"id": 2, "order": 1, "info": "3", "cover": {"thumbnail": "https://c/b.jpg"}},
                ]
            }
        )

        assert [cover.label for cover in covers] == ["Том 1", "Том 3"]


class TestParseChapters:
    def test_all_chapters_in_one_response(self, chapters_payload: Any) -> None:
        chapters = parse_chapters(chapters_payload)

        assert len(chapters) == 735

    def test_volume_and_number_are_read_as_strings(self, chapters_payload: Any) -> None:
        first = parse_chapters(chapters_payload)[0]

        assert isinstance(first.volume, int)
        assert first.number == "0"
        assert first.name == "Остаточное тепло начала"

    def test_branches_use_branch_id_not_id(self, chapters_payload: Any) -> None:
        """`id` внутри ветки совпадает с id главы; использовать его нельзя."""
        raw = chapters_payload["data"][0]["branches"]

        branches = parse_branches(raw)

        assert [b.id for b in branches] == [20944, 18996, 18997]
        assert branches[0].teams == ("DifferLex",)

    def test_sorted_and_labelled(self, chapters_payload: Any) -> None:
        chapters = parse_chapters(chapters_payload)

        assert [c.sort_key for c in chapters] == sorted(c.sort_key for c in chapters)
        assert all(c.label for c in chapters)

    def test_side_story_numbering(self, chapters_payload: Any) -> None:
        chapters = {c.id: c for c in parse_chapters(chapters_payload)}

        side_story = next(c for c in chapters.values() if c.number == "22.5")
        assert side_story.label == "1.22.5"
        assert side_story.name.startswith("Интерлюдия")

    def test_no_duplicate_labels_on_real_book(self, chapters_payload: Any) -> None:
        chapters = parse_chapters(chapters_payload)

        assert len({c.label for c in chapters}) == 735
        assert len({(c.volume, c.number) for c in chapters}) == 735

    def test_empty_payload(self) -> None:
        assert parse_chapters({"data": []}) == []
        assert parse_chapters({"data": None}) == []

    def test_dict_wrapped_payload_is_accepted(self) -> None:
        payload = {"data": {"chapters": [{"id": 1, "volume": "1", "number": "0", "name": "n"}]}}

        chapters = parse_chapters(payload)

        assert len(chapters) == 1
        assert chapters[0].label == "1.0"


class TestParseChapterContent:
    def test_doc_and_attachments(self) -> None:
        content = parse_chapter_content(load("chapter_images.json"), volume=1, number="0")

        assert content.available
        assert content.doc is not None
        assert content.doc["type"] == "doc"
        assert len(content.attachments) == 6
        assert content.volume == 1
        assert content.number == "0"

    def test_attachment_fields(self) -> None:
        content = parse_chapter_content(load("chapter_images.json"))
        first = content.attachments[0]

        assert first.name == "6e1c2c43-8162-41f6-be49-932258982643"
        assert first.width == 1852
        assert first.height == 2800
        assert first.url.startswith("/uploads/ranobe/94231/chapters/3422985/")

    def test_attachment_absolute_url_uses_site_origin(self) -> None:
        content = parse_chapter_content(load("chapter_images.json"))

        url = content.attachments[0].absolute_url()

        assert url.startswith("https://ranobelib.me/uploads/ranobe/94231/chapters/3422985/")

    def test_attachment_lookup_by_name(self) -> None:
        content = parse_chapter_content(load("chapter_images.json"))

        found = content.attachment_by_name("8ef3d556-d13b-4709-90bb-6520828aafb5")

        assert found is not None
        assert found.url.endswith("8ef3d556-d13b-4709-90bb-6520828aafb5.png")

    def test_attachment_lookup_miss(self) -> None:
        content = parse_chapter_content(load("chapter_images.json"))

        assert content.attachment_by_name("nope") is None

    def test_text_only_chapter(self) -> None:
        content = parse_chapter_content(load("chapter.json"), volume=1, number="22.5")

        assert content.available
        assert content.attachments == ()

    def test_pages_key_is_supported_for_manga(self) -> None:
        payload = {"data": {"content": {"type": "doc"}, "pages": [{"name": "a", "url": "/a.png"}]}}

        content = parse_chapter_content(payload)

        assert [a.name for a in content.attachments] == ["a"]

    def test_missing_content_marks_unavailable(self) -> None:
        content = parse_chapter_content({"data": {"attachments": []}})

        assert content.available is False
        assert content.doc is None

    def test_html_string_content_is_normalized(self) -> None:
        payload = {
            "data": {
                "content": (
                    '<p>Привет</p><p><img src="https://ranobelib.me/uploads/x/pic_1.png" /></p>'
                ),
                "attachments": [{"name": "pic_1", "url": "/uploads/x/pic_1.png"}],
            }
        }

        content = parse_chapter_content(payload)

        assert content.available
        assert content.doc is not None
        assert [node["type"] for node in content.doc["content"]] == ["paragraph", "paragraph"]
        image = content.doc["content"][1]["content"][0]
        assert image["attrs"]["images"] == [{"image": "pic_1"}]
