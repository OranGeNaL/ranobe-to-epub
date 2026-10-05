"""Тесты реестра модулей-источников и изоляции потребителей (book-source-api)."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from ranobelib_epub.models import Book, Chapter, ChapterContent, TranslationBranch
from ranobelib_epub.sources import (
    BookSource,
    SourceConfig,
    SourceModule,
    SourceRegistry,
    UnsupportedSourceError,
)


class DummySource:
    """Минимальная реализация контракта для проверки реестра."""

    name = "dummy"

    def __init__(self, config: SourceConfig) -> None:
        self.config = config

    def parse_url(self, url: str) -> str:
        return url.rstrip("/").split("/")[-1]

    async def fetch_book(self, ref: str) -> Book:
        return Book(slug_url=ref)

    async def fetch_chapters(self, ref: str) -> list:
        return []

    async def fetch_covers(self, ref: str) -> tuple:
        return ()

    async def fetch_chapter_content(self, ref: str, chapter: object, branch_id: int) -> object:
        raise NotImplementedError

    def resource_url(self, url: str) -> str:
        return url

    async def fetch_resource(self, url: str) -> bytes:
        return b""

    def chapter_unavailable_reason(self, chapter: object) -> str | None:
        return None

    def describe_failure(self, error: Exception, *, ref: str = "") -> str:
        return str(error)

    def is_authorization_error(self, error: Exception) -> bool:
        return False

    def requires_age_confirmation(self, book: Book) -> bool:
        return False

    def confirm_age(self, book: Book, report: object | None = None) -> str:
        return ""

    async def aclose(self) -> None:
        return None


def make_module() -> SourceModule:
    return SourceModule(
        name="dummy",
        matches=lambda url: "dummy.example" in url,
        parse=lambda url: url.rstrip("/").split("/")[-1],
        create=lambda config: DummySource(config),
    )


def test_dummy_satisfies_contract() -> None:
    assert isinstance(DummySource(SourceConfig()), BookSource)


def test_resolve_selects_matching_module() -> None:
    registry = SourceRegistry()
    registry.register(make_module())

    assert registry.resolve("https://dummy.example/book/1").name == "dummy"


def test_resolve_unsupported_link_raises() -> None:
    registry = SourceRegistry()
    registry.register(make_module())

    with pytest.raises(UnsupportedSourceError):
        registry.resolve("https://other.example/book/1")


def test_create_builds_configured_source() -> None:
    registry = SourceRegistry()
    registry.register(make_module())

    source = registry.create("https://dummy.example/book/1", SourceConfig(retries=7))

    assert isinstance(source, DummySource)
    assert source.config.retries == 7


class AltSource:
    """Альтернативный модуль-источник: доказывает, что сборка не знает про RanobeLib."""

    name = "alt"

    def parse_url(self, url: str) -> str:
        return "ref-1"

    async def fetch_book(self, ref: str) -> Book:
        return Book(slug_url=ref, rus_name="Альтернатива", name="Alt")

    async def fetch_chapters(self, ref: str) -> list[Chapter]:
        return [
            Chapter(
                id=1,
                volume=1,
                number="1",
                name="Глава",
                label="1.1",
                branches=(TranslationBranch(id=1, name="Перевод"),),
                branch_id=1,
            )
        ]

    async def fetch_covers(self, ref: str) -> tuple:
        return ()

    async def fetch_chapter_content(self, ref: str, chapter: Chapter, branch_id: int):
        return ChapterContent(
            doc={
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Текст"}]}
                ],
            },
            branch_id=branch_id,
            volume=chapter.volume,
            number=chapter.number,
        )

    def resource_url(self, url: str) -> str:
        return url

    async def fetch_resource(self, url: str) -> bytes:
        raise RuntimeError("в этом источнике нет иллюстраций")

    def chapter_unavailable_reason(self, chapter: Chapter) -> str | None:
        return None

    def describe_failure(self, error: Exception, *, ref: str = "") -> str:
        return str(error)

    def is_authorization_error(self, error: Exception) -> bool:
        return False

    def requires_age_confirmation(self, book: Book) -> bool:
        return False

    def confirm_age(self, book: Book, report: object | None = None) -> str:
        return ""

    async def aclose(self) -> None:
        return None


def test_alternative_source_satisfies_contract() -> None:
    assert isinstance(AltSource(), BookSource)


def test_build_runs_with_alternative_source(tmp_path: Path) -> None:
    """Сборка принимает источник, реализующий контракт, не зная конкретного сайта."""
    from ranobelib_epub.cli.main import Printer, run_build
    from ranobelib_epub.cli.options import Options

    target = tmp_path / "alt.epub"
    options = Options(
        slug_url="https://alt.example/book/1",
        output=target,
        include_images=False,
    )

    outcome = asyncio.run(
        run_build(options, Printer(stream=io.StringIO()), source=AltSource())
    )

    assert outcome.chapters_built == 1
    assert target.exists()
