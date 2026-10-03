"""Тесты фасада `RanobeLibSource` на фикстурах и подделке транспорта (задачи 4.1-4.6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from ranobelib_epub.source.api import RanobeLibSource, is_authorization_error, unavailable_reason
from ranobelib_epub.source.client import (
    AuthorizationRequiredError,
    BookNotFoundError,
    ClientConfig,
    RanobeLibClient,
)

FIXTURES = Path(__file__).parent / "fixtures"
SLUG = "94231--rezero-kara-hajimeru-isekai-seikatsu-outo-no-ichinichi-hen"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def build_source(
    handler: Any,
    *,
    retries: int = 1,
) -> tuple[RanobeLibSource, list[str]]:
    paths: list[str] = []
    transport = httpx.MockTransport(lambda request: _record(request, paths, handler))
    client = RanobeLibClient(
        ClientConfig(rate_limit=0, retries=retries),
        transport=transport,
    )
    return RanobeLibSource(client), paths


def _record(request: httpx.Request, paths: list[str], handler: Any) -> httpx.Response:
    paths.append(request.url.path)
    return handler(request)


def fixture_handler(name: str, status: int = 200) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=load(name))

    return handler


class TestFetchBook:
    async def test_metadata(self) -> None:
        source, _ = build_source(fixture_handler("book.json"))

        book = await source.fetch_book(SLUG)

        assert book.book_id == 94231
        assert book.rus_name is not None
        assert book.age_restriction_label == "18+"

    async def test_uses_single_request(self) -> None:
        source, paths = build_source(fixture_handler("book.json"))

        await source.fetch_book(SLUG)

        assert paths == [f"/api/manga/{SLUG}"]

    async def test_not_found(self) -> None:
        body = {"data": {"toast": {"type": "silent", "message": "Not Found"}}}
        source, _ = build_source(lambda r: httpx.Response(404, json=body))

        with pytest.raises(BookNotFoundError):
            await source.fetch_book(SLUG)


class TestFetchChapters:
    async def test_all_chapters_in_one_call(self) -> None:
        source, paths = build_source(fixture_handler("chapters.json"))

        chapters = await source.fetch_chapters(SLUG)

        assert len(chapters) == 735
        assert paths == [f"/api/manga/{SLUG}/chapters"], "пагинации нет — один запрос"

    async def test_chapters_are_numbered(self) -> None:
        source, _ = build_source(fixture_handler("chapters.json"))

        chapters = await source.fetch_chapters(SLUG)

        assert chapters[0].label is not None
        assert {c.label for c in chapters}.issuperset({"1.0", "1.22.5"})


class TestFetchChapterContent:
    async def test_content_and_attachments(self) -> None:
        source, _ = build_source(fixture_handler("chapter_images.json"))
        book_chapters, _ = build_source(fixture_handler("chapters.json"))
        chapter = (await book_chapters.fetch_chapters(SLUG))[0]

        content = await source.fetch_chapter_content(SLUG, chapter, 20944)

        assert content.available
        assert len(content.attachments) == 6

    async def test_volume_is_always_sent(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json=load("chapter_images.json"))

        source, _ = build_source(handler)
        book_chapters, _ = build_source(fixture_handler("chapters.json"))
        chapter = (await book_chapters.fetch_chapters(SLUG))[0]

        await source.fetch_chapter_content(SLUG, chapter, 20944)

        assert "volume=1" in seen[0], "без volume сервер отвечает 422"
        assert "branch_id=20944" in seen[0]

    async def test_fractional_number_is_sent_verbatim(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json=load("chapter.json"))

        source, _ = build_source(handler)
        book_chapters, _ = build_source(fixture_handler("chapters.json"))
        chapter = next(c for c in await book_chapters.fetch_chapters(SLUG) if c.number == "22.5")

        await source.fetch_chapter_content(SLUG, chapter, 18996)

        assert "number=22.5" in seen[0]


class TestUnavailableChapters:
    async def test_expired_chapter_is_not_requested(self) -> None:
        """Задача 4.6: глава с истёкшим доступом не инициирует ни одного запроса."""
        source, paths = build_source(fixture_handler("chapters.json"))
        chapters = await source.fetch_chapters(SLUG)
        chapter = chapters[0]
        chapter.expired_type = 2
        paths.clear()

        reason = unavailable_reason(chapter)
        content = None if reason else await source.fetch_chapter_content(SLUG, chapter, 20944)

        assert reason is not None and "expired_type=2" in reason
        assert content is None
        assert paths == [], "к недоступной главе не обращаемся"

    async def test_authorization_refusal_is_detected(self) -> None:
        body = {"data": {"message": "Not Authorized"}}

        source, paths = build_source(
            lambda r: httpx.Response(401, json=body),
            retries=5,
        )

        with pytest.raises(AuthorizationRequiredError) as info:
            await source.fetch_book(SLUG)

        assert is_authorization_error(info.value)
        assert len(paths) == 1, "отказ по авторизации не повторяется"

    async def test_regular_chapter_has_no_unavailable_reason(self) -> None:
        source, _ = build_source(fixture_handler("chapters.json"))
        chapters = await source.fetch_chapters(SLUG)

        assert unavailable_reason(chapters[0]) is None