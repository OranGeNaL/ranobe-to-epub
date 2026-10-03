"""Сборка контейнера EPUB 3 (задачи 10.1-10.8).

`mimetype` записывается первой и несжатой — этого требует OCF: часть читалок читает
именно её, не разжимая архив, и любая другая запись на её месте делает файл
нечитаемым. Порядок остальных записей фиксирован, чтобы две сборки одной книги давали
байт-в-байт одинаковый архив.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..models import Book
from .naming import sanitize
from .parts import (
    CONTAINER_PATH,
    NAV_PATH,
    NCX_PATH,
    PACKAGE_PATH,
    ChapterEntry,
    chapter_document,
    container_xml,
    nav_xhtml,
    package_opf,
    toc_ncx,
)

MIMETYPE = "mimetype"
MIMETYPE_VALUE = "application/epub+zip"

#: Каталог пакета: `href` в OPF разрешаются относительно него, поэтому всё содержимое
#: лежит внутри `EPUB/`, а в манифесте указываются пути вида `TEXT/1.25_Пролог.html`.
OPF_DIR = "EPUB"
TEXT_DIR = f"{OPF_DIR}/TEXT"
IMAGES_DIR = f"{OPF_DIR}/Images"


@dataclass(slots=True)
class ChapterDocument:
    """Готовая глава: имя файла, XHTML и заголовок для навигации."""

    filename: str
    title: str
    xhtml: str


@dataclass(slots=True)
class BuildResult:
    """Итог сборки."""

    path: Path
    entries: int
    chapters: int
    images: int
    size: int
    files: list[str] = field(default_factory=list)


class EpubBuilder:
    """Накапливает части книги и упаковывает их в EPUB."""

    def __init__(self, book: Book, build_date: date | None = None) -> None:
        self.book = book
        self.build_date = build_date
        self._chapters: list[ChapterDocument] = []
        self._images: dict[str, tuple[bytes, str]] = {}
        self._cover: tuple[str, str] | None = None

    def add_chapter(self, document: ChapterDocument) -> None:
        self._chapters.append(document)

    def add_image(self, filename: str, data: bytes, mime: str) -> None:
        self._images[filename] = (data, mime)

    def set_cover(self, filename: str, mime: str = "image/jpeg") -> None:
        self._cover = (filename, mime)

    @property
    def chapters(self) -> list[ChapterDocument]:
        return list(self._chapters)

    def entries(self) -> list[ChapterEntry]:
        """Главы в порядке сборки — единый источник для навигации и NCX.

        В `href` путь относителен каталогу OPF, как требует EPUB 3.
        """
        return [
            ChapterEntry(
                title=chapter.title,
                href=chapter.filename,
                play_order=index,
            )
            for index, chapter in enumerate(self._chapters, start=1)
        ]

    def write(self, path: Path | str) -> BuildResult:
        """Собирает EPUB и возвращает результат с числом записей и размером."""
        target = Path(path)
        entries = self.entries()

        # По умолчанию zipfile пишет всё без сжатия; EPUB ожидает deflate для всех
        # записей, кроме `mimetype`, который обязан остаться распакованным.
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            # 10.1: mimetype — первая запись, ZIP_STORED.
            info = zipfile.ZipInfo(MIMETYPE)
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, MIMETYPE_VALUE)

            archive.writestr(CONTAINER_PATH, container_xml())
            archive.writestr(
                PACKAGE_PATH,
                package_opf(
                    self.book,
                    entries,
                    images=[(href, mime) for href, (_, mime) in self._images.items()],
                    cover_item=self._cover[0] if self._cover else None,
                    cover_media_type=self._cover[1] if self._cover else "image/jpeg",
                    build_date=self.build_date,
                ),
            )
            archive.writestr(NAV_PATH, nav_xhtml(self.book, entries))
            archive.writestr(NCX_PATH, toc_ncx(entries, self.book))

            for filename, (data, _) in self._images.items():
                archive.writestr(_in_package(filename), data)

            for chapter in self._chapters:
                archive.writestr(_in_package(chapter.filename), chapter.xhtml)

        with zipfile.ZipFile(target) as archive:
            files = archive.namelist()
            size = target.stat().st_size

        return BuildResult(
            path=target,
            entries=len(files),
            chapters=len(self._chapters),
            images=len(self._images),
            size=size,
            files=files,
        )


def _in_package(href: str) -> str:
    """Путь внутри архива: `href` из манифеста плюс каталог пакета `EPUB/`."""
    if href.startswith(f"{OPF_DIR}/"):
        return href
    return f"{OPF_DIR}/{href.lstrip('/')}"


def manifest_href(filename: str) -> str:
    """Путь для манифеста: относительно каталога OPF, без префикса `EPUB/`."""
    if filename.startswith(f"{OPF_DIR}/"):
        return filename.removeprefix(f"{OPF_DIR}/")
    return filename


def make_chapter(title: str, fragment: str, language: str | None = None) -> ChapterDocument:
    """Готовая XHTML-страница главы с её заголовком и безопасным именем."""
    safe = sanitize(title)
    document = chapter_document(safe, fragment, language)
    return ChapterDocument(filename="", title=title, xhtml=document)


def expected_parts() -> tuple[str, ...]:
    """Обязательные части EPUB — используется в проверках (10.8)."""
    return (MIMETYPE, CONTAINER_PATH, PACKAGE_PATH, NAV_PATH, NCX_PATH)


def iter_chapter_files(result: BuildResult) -> Iterable[str]:
    """Файлы глав внутри архива в порядке сборки."""
    return (name for name in result.files if name.startswith(f"{TEXT_DIR}/"))
