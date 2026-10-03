"""Параллельная загрузка глав и потоковая сборка (решение 8, задачи 12.1-12.4).

Сеть идёт через `asyncio` с ограничением параллелизма, а троттлинг остаётся в клиенте:
он общий для всех задач, поэтому суммарная частота запросов не зависит от числа
одновременных загрузок. Тяжёлое сжатие Pillow уходит в поток через `asyncio.to_thread` —
иначе event loop вставал бы на сотни миллисекунд и TUI переставал бы перерисовываться.

Записи в архив идут по мере готовности глав, поэтому память растёт от числа картинок, а
не от числа глав.
"""

from __future__ import annotations

import asyncio
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ..charset.filter import DEFAULT_PROFILE, FilterResult, filter_document
from ..convert.tiptap import ConversionResult, convert_document
from ..images.pipeline import (
    DEFAULT_MAX_MB,
    DEFAULT_MAX_WIDTH,
    DEFAULT_QUALITY,
    ImageAsset,
    absolute_url,
    chapter_image_href,
    collect_image_keys,
    epub_filename,
    fetch_image,
)
from ..models import Book, Chapter, ChapterContent
from ..source.api import RanobeLibSource, unavailable_reason
from ..source.translations import Coverage
from .report import DownloadOutcome, Progress, ReportRecorder

DEFAULT_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class ChapterTask:
    """Единица загрузки: глава плюс параметры сборки."""

    chapter: Chapter
    charset: str = DEFAULT_PROFILE
    include_images: bool = True


@dataclass(slots=True)
class FetchedChapter:
    """Глава, готовая к упаковке."""

    chapter: Chapter
    fragment: str
    title: str
    filters: FilterResult
    conversion: ConversionResult
    assets: list[ImageAsset] = field(default_factory=list)


class ChapterDownloader:
    """Загружает главы параллельно, не теряя ни одну из-за сбоя."""

    def __init__(
        self,
        source: RanobeLibSource,
        recorder: ReportRecorder,
        concurrency: int = DEFAULT_CONCURRENCY,
        on_progress: Callable[[Progress], None] | None = None,
        on_cover: Callable[[str], None] | None = None,
        max_image_mb: float = DEFAULT_MAX_MB,
        max_image_width: int = DEFAULT_MAX_WIDTH,
        quality: int = DEFAULT_QUALITY,
        include_images: bool = True,
    ) -> None:
        self.source = source
        self.recorder = recorder
        self.semaphore = asyncio.Semaphore(max(1, concurrency))
        self.on_progress = on_progress
        self.on_cover = on_cover
        self.progress = Progress()
        self.max_image_mb = max_image_mb
        self.max_image_width = max_image_width
        self.quality = quality
        self.include_images = include_images
        self._image_index = 0

    async def _load(self, task: ChapterTask) -> ChapterContent | None:
        """Сетевой шаг: содержимое главы или `None` с записью причины в отчёт."""
        chapter = task.chapter

        static = unavailable_reason(chapter)
        if static:
            self.recorder.chapter_unavailable(chapter, static)
            return None

        if chapter.branch_id is None:
            self.recorder.chapter_unavailable(chapter, "нет ветки перевода")
            return None

        async with self.semaphore:
            try:
                content = await self.source.fetch_chapter_content(
                    self.book_slug, chapter, chapter.branch_id
                )
            except Exception as error:  # причина уходит в отчёт, глава пропускается
                self.recorder.chapter_unavailable(chapter, _reason(error))
                return None
        return content

    async def _finalize(self, task: ChapterTask, content: ChapterContent) -> FetchedChapter:
        """Фильтрация, изображения и конвертация главы в XHTML."""
        chapter = task.chapter
        filters = filter_document(content.doc, task.charset)
        renderer = None
        assets: list[ImageAsset] = []
        if task.include_images:
            resolver, assets = await self._prepare_images(content, chapter)
            # Отсутствующий ключ даёт пустой `src`: конвертер поставит текстовую
            # заглушку. Без обработки картинок `resolver` не передаётся вовсе, иначе
            # конвертер принял бы его за функцию и заглушил все `src`.
            renderer = resolver.get
        conversion = convert_document(filters.document, resolver=renderer)
        for warning in conversion.warnings:
            self.recorder.unknown_node(warning)
        self.recorder.filtered(filters.stats)
        self.recorder.chapter_built()

        return FetchedChapter(
            chapter=chapter,
            fragment=conversion.html,
            title=_chapter_title(chapter),
            filters=filters,
            conversion=conversion,
            assets=assets,
        )

    async def fetch_one(self, task: ChapterTask) -> FetchedChapter | None:
        """Одна глава; `None` — глава попала в отчёт как недоступная."""
        content = await self._load(task)
        if content is None:
            return None
        return await self._finalize(task, content)

    async def _prepare_images(
        self, content: ChapterContent, chapter: Chapter
    ) -> tuple[dict[str, str], list[ImageAsset]]:
        """Скачивает и сжимает иллюстрации главы, отдавая `key -> src` для конвертера.

        Отсутствующая или недоступная картинка не прерывает главу: ключ получает пустой
        `src`, конвертер ставит текстовую заглушку, а причина уходит в отчёт.
        """
        keys = collect_image_keys(content.doc)
        if not keys:
            return {}, []

        index = {item.name: item for item in content.attachments}
        resolver: dict[str, str] = {}
        assets: list[ImageAsset] = []
        label = chapter.label or f"{chapter.volume}.{chapter.number}"
        self.progress.images_total += len(keys)

        for key in keys:
            attachment = index.get(key)
            url = absolute_url(attachment) if attachment else ""
            if not attachment or not url:
                self.recorder.image_missing(url or None, "вложение не найдено", label, key)
                resolver[key] = ""
                self.progress.images_done += 1
                continue

            try:
                raw = await fetch_image(self.source.client, url)
                asset = await compress_in_thread(
                    raw, self.max_image_mb, self.max_image_width, self.quality
                )
            except Exception as error:
                self.recorder.image_missing(url, _reason(error), label, key)
                resolver[key] = ""
                self.progress.images_done += 1
                continue

            self._image_index += 1
            asset.filename = epub_filename(self._image_index, asset.mime)
            asset.source_url = url
            resolver[key] = chapter_image_href(asset.filename)
            assets.append(asset)
            self.progress.images_done += 1

        return resolver, assets

    async def fetch_cover(self, book: Book) -> ImageAsset | None:
        """Обложка книги, если она объявлена и доступна."""
        if not self.include_images:
            return None
        url = absolute_url({"url": book.cover or ""})
        if not url:
            return None
        try:
            raw = await fetch_image(self.source.client, url)
            asset = await compress_in_thread(
                raw, self.max_image_mb, self.max_image_width, self.quality
            )
        except Exception as error:
            self.recorder.image_missing(url, _reason(error), "обложка", "cover")
            return None
        self._image_index += 1
        asset.filename = epub_filename(self._image_index, asset.mime)
        asset.source_url = url
        if self.on_cover is not None:
            self.on_cover(asset.filename)
        return asset

    book_slug: str = ""

    async def fetch_all(
        self,
        tasks: Iterable[ChapterTask],
        book_slug: str,
        cancel: asyncio.Event | None = None,
    ) -> list[FetchedChapter]:
        """Все главы по порядку; недоступные не прерывают остальные.

        Загрузка идёт двумя фазами. Сначала содержимое всех глав скачивается
        параллельно — это самая долгая часть. Затем главы обрабатываются строго по
        порядку: только так имена картинок (`img_0001`, `img_0002`, …) зависят от
        порядка чтения, а не от того, какая глава успела первой, и повторная сборка
        даёт тот же архив.
        """
        self.book_slug = book_slug
        items = list(tasks)
        self.progress = Progress(total=len(items), _started=time.monotonic())
        loaded: list[ChapterContent | None] = [None] * len(items)

        async def load(index: int, task: ChapterTask) -> None:
            if cancel is not None and cancel.is_set():
                return
            loaded[index] = await self._load(task)

        await asyncio.gather(*(load(index, task) for index, task in enumerate(items)))

        results: list[FetchedChapter] = []
        for index, content in enumerate(loaded):
            if content is None:
                continue
            if cancel is not None and cancel.is_set():
                break
            task = items[index]
            results.append(await self._finalize(task, content))
            self.progress.done += 1
            self.progress.current_label = task.chapter.label or ""
            self.progress.elapsed = max(0.0, time.monotonic() - self.progress._started)
            if self.on_progress is not None:
                # Отдаётся снимок, а не сам объект: подписчик TUI может сохранять
                # обновления, и общий изменяемый экземпляр показывал бы ему
                # последнее состояние вместо состояния на момент события.
                self.on_progress(self.progress.snapshot())
        return results


def _chapter_title(chapter: Chapter) -> str:
    label = chapter.label or f"{chapter.volume}.{chapter.number}"
    return f"{label} {chapter.name}".strip()


def _reason(error: Exception) -> str:
    message = str(error).strip()
    return message or type(error).__name__


async def compress_in_thread(
    raw: bytes,
    max_image_mb: float,
    max_width: int,
    quality: int = 80,
) -> ImageAsset:
    """Сжатие Pillow вне event loop (12.2)."""
    from ..images.pipeline import filter_and_compress

    return await asyncio.to_thread(
        filter_and_compress, raw, max_image_mb, max_width, quality
    )


class StreamingWriter:
    """Пишет главы в архив по мере поступления (12.3)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._archive: zipfile.ZipFile | None = None
        self._image_index = 0
        self.chapters_written = 0
        self.images_written = 0

    def __enter__(self) -> StreamingWriter:
        self._archive = zipfile.ZipFile(self.path, "w", compression=zipfile.ZIP_DEFLATED)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._archive is not None:
            self._archive.close()
            self._archive = None

    def add_image(self, asset: ImageAsset) -> str:
        """Добавляет картинку и возвращает её `href` для главы."""
        if self._archive is None:
            raise RuntimeError("архив не открыт")
        self._image_index += 1
        href = epub_filename(self._image_index, asset.mime)
        self._archive.writestr(f"EPUB/{href}", asset.data)
        self.images_written += 1
        return href

    def href_for_chapter(self, filename: str) -> str:
        """Путь к картинке относительно каталога главы."""
        return chapter_image_href(filename)

    def add_chapter(self, filename: str, xhtml: str) -> None:
        if self._archive is None:
            raise RuntimeError("архив не открыт")
        self._archive.writestr(filename, xhtml)
        self.chapters_written += 1


@dataclass(slots=True)
class ChapterSelector:
    """Выборка глав из `--chapters` (задача 13.2)."""

    labels: frozenset[str] = frozenset()
    volumes: frozenset[int] = frozenset()
    indexes: frozenset[int] = frozenset()

    def matches(self, chapter: Chapter, index: int) -> bool:
        """Пустая выборка означает «все главы»."""
        if not (self.labels or self.volumes or self.indexes):
            return True
        if self.volumes and chapter.volume in self.volumes:
            return True
        return bool(self.indexes and index in self.indexes) or bool(
            self.labels and chapter.label and chapter.label in self.labels
        )

    def apply(self, chapters: list[Chapter]) -> list[Chapter]:
        return [
            chapter
            for index, chapter in enumerate(chapters, start=1)
            if self.matches(chapter, index)
        ]


def coverage_note(coverage: Coverage) -> str:
    """Строка предупреждения о неполном покрытии перевода (14.3)."""
    if not coverage.is_partial:
        return f"Перевод «{coverage.team}» покрывает все {coverage.total} глав"
    return (
        f"Перевод «{coverage.team}» покрывает {coverage.covered} из {coverage.total} глав; "
        "остальные будут скачаны в актуальной ветке"
    )


ProgressCallback = Callable[[Progress], None]
AwaitableFactory = Callable[[], Awaitable[None]]
DownloadOutcomeList = list[DownloadOutcome]