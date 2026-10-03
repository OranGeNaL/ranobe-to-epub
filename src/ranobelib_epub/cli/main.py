"""Точка входа: разбор аргументов, сборка, отчёт и код возврата (задачи 13.3, 13.4).

Программа работает и без терминала: TUI включается по умолчанию, но `--no-tui` и
перенаправление вывода дают тот же результат в текстовом виде. Ненулевой код возврата
означает ошибку, нулевой — успех, в том числе при частичных потерях: собранный файл
всё равно полезен, а потери перечислены в отчёте.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..images.pipeline import ImageAsset
from ..models import Book, Chapter, Cover, apply_overrides, resolve_cover_url
from ..pipeline.downloader import (
    ChapterDownloader,
    ChapterTask,
)
from ..pipeline.report import Progress, ReportRecorder
from ..source.age import confirm_age, requires_confirmation
from ..source.api import RanobeLibSource
from ..source.client import (
    ApiError,
    BookNotFoundError,
    ClientConfig,
    RanobeLibClient,
)
from ..source.numbering import assign_labels, sort_chapters
from ..source.translations import (
    apply_selection,
    available_teams,
    compute_coverage,
)
from ..source.url import parse_book_url
from .options import ArgumentError, Options, apply_chapter_selection, parse_args

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_ARGUMENT = 2


@dataclass(slots=True)
class BuildOutcome:
    """Результат одной сборки."""

    output: Path
    size: int
    chapters_built: int
    chapters_total: int


class Printer:
    """Вывод в stdout: прогресс построчно, без курсорных escape-последовательностей."""

    def __init__(self, stream=None, quiet: bool = False) -> None:
        self.stream = stream or sys.stdout
        self.quiet = quiet

    def line(self, text: str = "") -> None:
        if self.quiet:
            return
        print(text, file=self.stream, flush=True)

    def error(self, text: str) -> None:
        print(f"Ошибка: {text}", file=sys.stderr, flush=True)


def build_config(options: Options) -> ClientConfig:
    return ClientConfig(rate_limit=options.rate_limit, retries=options.retries)


def progress_line(progress: Progress) -> str:
    """Строка журнала по последней главе; без события — прежний агрегат (решение 6)."""
    if progress.last_event is not None:
        return progress.last_event.render()
    return progress.render()


def journal_line(printer: Printer, progress: Progress) -> None:
    """Неинтерактивный режим печатает строку на главу, а не промежуточный прогресс."""
    if progress.last_event is not None:
        printer.line(progress_line(progress))


async def collect(
    source: RanobeLibSource,
    slug: str,
    recorder: ReportRecorder,
    printer: Printer,
) -> tuple[Book, list[Chapter]]:
    """Метаданные, главы и подтверждение возраста.

    Выборка глав здесь не применяется: её задаёт пользователь (в том числе в TUI),
    поэтому она учитывается перед формированием задач в `run_build` (задача 2.2).
    """
    book = await source.fetch_book(slug)
    chapters = await source.fetch_chapters(slug)
    assign_labels(chapters)
    chapters = sort_chapters(chapters)

    if requires_confirmation(book):
        message = confirm_age(book, recorder.report)
        printer.line(f"{book.rus_name or book.name}: {message}")

    return book, chapters


async def run_build(
    options: Options,
    printer: Printer,
    client: RanobeLibClient | None = None,
) -> BuildOutcome:
    """Полная сборка: сеть → фильтр → конвертация → EPUB → отчёт."""
    if not options.slug_url:
        raise ArgumentError("не указана ссылка на книгу")

    slug = parse_book_url(options.slug_url)
    owns_client = client is None
    active = client or RanobeLibClient(build_config(options))
    recorder = ReportRecorder()

    try:
        source = RanobeLibSource(active)
        book, chapters = await collect(source, slug, recorder, printer)

        chapters = apply_chapter_selection(options, chapters)
        apply_selection(chapters, options.team)
        recorder.report.total_chapters = len(chapters)

        coverage = compute_coverage(chapters, options.team)
        if coverage.is_partial:
            printer.line(
                f"Внимание: выбранный перевод покрывает {coverage.covered} из {coverage.total} глав"
            )

        effective_book = apply_overrides(book, options.metadata_overrides)

        covers: tuple[Cover, ...] = ()
        if options.cover_id is not None:
            covers = await source.fetch_covers(slug)
        cover_url = resolve_cover_url(book, covers, options.cover_id, options.cover_disabled)
        if options.cover_id is not None:
            chosen = next((item for item in covers if item.id == options.cover_id), None)
            if chosen is None or not chosen.url:
                recorder.report.notes.append(
                    f"выбранная обложка {options.cover_id} недоступна; EPUB собран без обложки"
                )

        printer.line(
            f"Книга: {book.title} · глав: {len(chapters)} · "
            f"переводов: {len(available_teams(chapters))}"
        )

        downloader = ChapterDownloader(
            source,
            recorder,
            on_progress=lambda progress: journal_line(printer, progress),
            max_image_mb=options.max_image_mb,
            max_image_width=options.max_image_width,
            quality=options.quality,
            grayscale=options.grayscale,
            include_images=options.include_images,
        )
        tasks = [
            ChapterTask(chapter, charset=options.charset, include_images=options.include_images)
            for chapter in chapters
        ]
        fetched = await downloader.fetch_all(tasks, book_slug=slug)

        target = options.output_path_for(effective_book)
        target.parent.mkdir(parents=True, exist_ok=True)

        cover = await downloader.fetch_cover(cover_url)
        build_date = None
        if options.date:
            build_date = date.fromisoformat(options.date)
        size = write_epub(
            target, effective_book, fetched, options, recorder, cover=cover, build_date=build_date
        )

        printer.line()
        printer.line(recorder.render())

        return BuildOutcome(
            output=target,
            size=size,
            chapters_built=recorder.report.built_chapters,
            chapters_total=len(chapters),
        )
    finally:
        if owns_client:
            await active.aclose()


def write_epub(
    target: Path,
    book: Book,
    fetched: list,
    options: Options,
    recorder: ReportRecorder,
    cover: ImageAsset | None = None,
    build_date: date | None = None,
) -> int:
    """Собирает EPUB из готовых глав и завершает отчёт."""
    from ..epub.builder import ChapterDocument, EpubBuilder
    from ..epub.naming import chapter_filename
    from ..epub.parts import chapter_document

    builder = EpubBuilder(book, build_date=build_date)
    taken: set[str] = set()

    for index, item in enumerate(fetched, start=1):
        filename = chapter_filename(item.chapter.label, item.chapter.name, index, taken)
        builder.add_chapter(
            ChapterDocument(
                filename=filename,
                title=item.title,
                xhtml=chapter_document(item.title, item.fragment, book.in_language),
            )
        )
        for asset in item.assets:
            builder.add_image(asset.filename, asset.data, asset.mime)

    if cover is not None:
        builder.add_image(cover.filename, cover.data, cover.mime)
        builder.set_cover(cover.filename, cover.mime)

    result = builder.write(target)
    recorder.finished(str(result.path), result.size)
    return result.size


def should_use_tui(options: Options) -> bool:
    """Интерактивный режим — по умолчанию, но только в настоящем терминале.

    Перенаправленный вывод означает скрипт или CI: там TUI не запускается, потому что
    ждать ввода некому, а прогресс и так уходит в stdout строкой.
    """
    if options.no_tui:
        return False
    return sys.stdin.isatty() and sys.stdout.isatty()


async def list_covers_flow(
    options: Options,
    printer: Printer,
    client: RanobeLibClient | None = None,
) -> None:
    """`--list-covers`: показывает обложки карусели и завершается без сборки (задача 5.2)."""
    if not options.slug_url:
        raise ArgumentError("не указана ссылка на книгу")

    slug = parse_book_url(options.slug_url)
    owns_client = client is None
    active = client or RanobeLibClient(build_config(options))
    try:
        source = RanobeLibSource(active)
        book = await source.fetch_book(slug)
        covers = await source.fetch_covers(slug)
    finally:
        if owns_client:
            await active.aclose()

    printer.line(f"Книга: {book.title}")
    if not covers:
        printer.line("Доступных обложек нет; будет использована обложка книги.")
        return
    for index, cover in enumerate(covers, start=1):
        printer.line(f"{index}. id={cover.id} — {cover.label}")


def run_tui_mode(options: Options) -> int:
    """Запуск интерактивного интерфейса (задачи 14.1-14.10)."""
    from ..tui.app import ExporterApp

    ExporterApp(options).run()
    return EXIT_OK


def configure_output_encoding() -> None:
    """UTF-8 для stdout/stderr: русский вывод не падает при редиректе на Windows.

    Python выбирает кодировку stdout по консоли или локали: на Windows с
    перенаправленным выводом это cp1252, и нелатинские символы в справке или
    журнале роняют программу (UnicodeEncodeError). Переключение на UTF-8 с
    заменой неожиданных символов чинит и скомпилированный бинарь, и `uv run`.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа консольного скрипта (13.4, 13.6)."""
    configure_output_encoding()
    printer = Printer()
    try:
        options = parse_args(list(argv) if argv is not None else None)
    except ArgumentError as error:
        printer.error(str(error))
        return EXIT_BAD_ARGUMENT
    except SystemExit as error:  # --help и ошибки argparse
        code = error.code
        return code if isinstance(code, int) else EXIT_BAD_ARGUMENT

    if options.list_covers:
        try:
            asyncio.run(list_covers_flow(options, printer))
        except BookNotFoundError as error:
            printer.error(f"книга не найдена: {error}")
            return EXIT_FAILED
        except (ApiError, ArgumentError) as error:
            printer.error(str(error))
            return EXIT_FAILED
        return EXIT_OK

    if should_use_tui(options):
        return run_tui_mode(options)

    try:
        outcome = asyncio.run(run_build(options, printer))
    except BookNotFoundError as error:
        printer.error(f"книга не найдена: {error}")
        return EXIT_FAILED
    except (ApiError, ArgumentError) as error:
        printer.error(str(error))
        return EXIT_FAILED
    except KeyboardInterrupt:
        printer.error("сборка прервана пользователем")
        return EXIT_FAILED

    printer.line(f"Готово: {outcome.output} ({outcome.chapters_built} глав)")
    return EXIT_OK


def run() -> None:
    """Обёртка для `console_scripts`: завершает процесс нужным кодом."""
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover - ручной запуск модуля
    run()
