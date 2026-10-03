"""Интерактивный терминальный интерфейс на `textual` (решение 8, задачи 14.1-14.10).

Экран — это шаг сценария, а решения и данные живут на самом приложении, поэтому тесты
могут подменять загрузку метаданных и сборку, не поднимая сеть и не зависая на
терминале. Прогресс из сетевого слоя идёт через `asyncio.Queue`: сборка не обновляет
виджеты напрямую, а складывает снимки, которые интерфейс разбирает по таймеру. Так
event loop остаётся свободным, и окно перерисовывается даже при медленном сервере.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, OptionList, ProgressBar, Static
from textual.widgets.option_list import Option

from ..cli.options import Options
from ..models import Book, Chapter
from ..pipeline.downloader import coverage_note
from ..pipeline.report import Progress, ReportRecorder
from ..source.translations import available_teams, compute_coverage
from ..source.url import InvalidBookUrlError, parse_book_url

DEFAULT_OPTION = "__default__"
DEFAULT_LABEL = "Актуальная ветка (по умолчанию)"


@dataclass(slots=True)
class Metadata:
    """Книга и её главы, полученные до выбора перевода."""

    book: Book
    chapters: list[Chapter]
    slug: str = ""


@dataclass(slots=True)
class BuildPlan:
    """Все решения пользователя, необходимые для сборки."""

    book: Book
    chapters: list[Chapter]
    options: Options
    slug: str
    team: str | None = None


ProgressSink = Callable[[Progress], None]
NoticeSink = Callable[[str], None]
BuildFunction = Callable[[BuildPlan, ProgressSink, NoticeSink], Awaitable[ReportRecorder]]
LoadFunction = Callable[[str, Options], Awaitable[Metadata]]
SaveFunction = Callable[[], Awaitable[Path]]


class ExporterApp(App[None]):
    """Приложение: хранит состояние сценария и переключает экраны."""

    CSS = """
    Screen { padding: 1 2; }
    #error { color: $error; }
    #warning { color: $warning; }
    #prompt { padding-bottom: 1; }
    .actions { height: auto; padding-top: 1; }
    .actions Button { margin-right: 2; }
    #details_box { height: auto; }
    #report { height: 1fr; }
    """

    BINDINGS: ClassVar = [("q", "quit", "Выход")]

    def __init__(
        self,
        options: Options | None = None,
        *,
        load_metadata: LoadFunction | None = None,
        build: BuildFunction | None = None,
        save_partial: SaveFunction | None = None,
    ) -> None:
        super().__init__()
        self.options = options or Options()
        self.metadata: Metadata | None = None
        self.team: str | None = None
        self.report_text: str = ""
        self.interrupted: bool = False
        self.progress_queue: asyncio.Queue[Progress] = asyncio.Queue()
        self.save_partial = save_partial
        self._load = load_metadata or default_load_metadata
        self._build = build or default_build

    def on_mount(self) -> None:
        self.push_screen(LinkScreen())

    async def load_metadata(self, link: str, options: Options | None = None) -> Metadata:
        metadata = await self._load(link, options or self.options)
        self.metadata = metadata
        return metadata

    async def run_build(self, on_progress: ProgressSink, on_notice: NoticeSink) -> ReportRecorder:
        if self.metadata is None:
            raise RuntimeError("метаданные не загружены")
        plan = BuildPlan(
            book=self.metadata.book,
            chapters=self.metadata.chapters,
            options=self.options,
            slug=self.metadata.slug,
            team=self.team,
        )
        return await self._build(plan, on_progress, on_notice)


class LinkScreen(Screen[None]):
    """Шаг 1: ссылка на книгу с проверкой без сети (14.1)."""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Введите ссылку на книгу ranobelib.me", id="prompt")
            yield Input(
                placeholder="https://ranobelib.me/book/94231--...",
                id="link",
            )
            with Horizontal(classes="actions"):
                yield Button("Дальше", id="submit", variant="primary")
            yield Static("", id="error")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#link", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self._submit()

    def _show_error(self, message: str) -> None:
        self.query_one("#error", Static).update(message)

    def _submit(self) -> None:
        link = self.query_one("#link", Input).value
        try:
            parse_book_url(link)
        except InvalidBookUrlError as error:
            self._show_error(str(error))
            return
        self._show_error("")
        self.run_worker(self._load(link), exclusive=True)

    async def _load(self, link: str) -> None:
        app = cast(ExporterApp, self.app)
        try:
            metadata = await app.load_metadata(link)
        except Exception as error:  # причина показывается на этом же шаге
            self._show_error(f"Не удалось получить книгу: {error}")
            return
        if self._has_choice(metadata.chapters):
            self.app.push_screen(TranslationScreen())
        else:
            self.app.push_screen(ConfirmScreen())

    @staticmethod
    def _has_choice(chapters: list[Chapter]) -> bool:
        """Есть ли у книги больше одной ветки (14.4)."""
        return len(available_teams(chapters)) > 1


class TranslationScreen(Screen[None]):
    """Шаг 2: выбор перевода с покрытием и предупреждением (14.2, 14.3, 14.5)."""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Выберите перевод", id="prompt")
            yield OptionList(id="teams")
            yield Static("", id="coverage")
            yield Static("", id="warning")
            with Horizontal(classes="actions"):
                yield Button("Продолжить", id="continue", variant="primary")
                yield Button("Показать непокрытые", id="details")
                yield Button("Назад", id="back")
            yield VerticalScroll(Static("", id="details_text"), id="details_box")
        yield Footer()

    def on_mount(self) -> None:
        app = cast(ExporterApp, self.app)
        teams = available_teams(app.metadata.chapters) if app.metadata else ()
        options = [_option(DEFAULT_OPTION, DEFAULT_LABEL)]
        options.extend(
            _option(team.name, _team_label(team.name, team.covered, team.total))
            for team in teams
        )
        option_list = self.query_one("#teams", OptionList)
        option_list.add_options(options)
        option_list.highlighted = 0
        self._refresh(DEFAULT_OPTION)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._refresh(str(event.option_id))

    def _refresh(self, option_id: str) -> None:
        app = cast(ExporterApp, self.app)
        team = None if option_id == DEFAULT_OPTION else option_id
        coverage = compute_coverage(app.metadata.chapters, team)
        self.query_one("#coverage", Static).update(coverage_note(coverage))
        warning = self.query_one("#warning", Static)
        if coverage.is_partial:
            warning.update(
                f"Внимание: перевод покрывает {coverage.covered} из {coverage.total} глав. "
                "Остальные будут скачаны в актуальной ветке."
            )
        else:
            warning.update("")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = cast(ExporterApp, self.app)
        if event.button.id == "continue":
            app.team = self._selected_team()
            app.push_screen(ConfirmScreen())
        elif event.button.id == "back":
            app.pop_screen()
        elif event.button.id == "details":
            self._show_uncovered()

    def _selected_team(self) -> str | None:
        option_list = self.query_one("#teams", OptionList)
        option = option_list.get_option_at_index(option_list.highlighted or 0)
        return None if str(option.id) == DEFAULT_OPTION else str(option.id)

    def _show_uncovered(self) -> None:
        app = cast(ExporterApp, self.app)
        coverage = compute_coverage(app.metadata.chapters, self._selected_team())
        target = self.query_one("#details_text", Static)
        if not coverage.uncovered_labels:
            target.update("Непокрытых глав нет.")
            return
        labels = ", ".join(coverage.uncovered_labels)
        target.update(f"Непокрытые главы ({len(coverage.uncovered_labels)}): {labels}")


class ConfirmScreen(Screen[None]):
    """Шаг 3: параметры и запуск сборки."""

    def compose(self) -> ComposeResult:
        app = cast(ExporterApp, self.app)
        lines = app.options.summary_lines()
        book = app.metadata.book if app.metadata else None
        chapters = app.metadata.chapters if app.metadata else []
        text = "\n".join(
            [
                f"Книга: {book.title if book else '—'}",
                f"Глав: {len(chapters)}",
                f"Перевод: {app.team or DEFAULT_LABEL}",
                "",
                "Параметры:",
                *lines,
            ]
        )
        with Vertical():
            yield Static("Подтверждение", id="prompt")
            yield Static(text, id="summary")
            with Horizontal(classes="actions"):
                yield Button("Начать", id="start", variant="primary")
                yield Button("Отмена", id="cancel")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.app.push_screen(ProgressScreen())
        elif event.button.id == "cancel":
            self.app.exit()


class ProgressScreen(Screen[None]):
    """Шаг 4: ход сборки, отдельный индикатор картинок (14.6, 14.7)."""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Сборка книги", id="prompt")
            yield ProgressBar(total=100, id="chapters")
            yield Static("", id="chapter_info")
            yield ProgressBar(total=100, id="images")
            yield Static("", id="images_info")
            yield Static("", id="notices")
        yield Footer()

    def on_mount(self) -> None:
        self.last: Progress = Progress()
        self.set_interval(0.05, self._drain)
        self.run_worker(self._run(), exclusive=True, name="build")

    async def _run(self) -> None:
        app = cast(ExporterApp, self.app)
        notices: list[str] = []
        try:
            recorder = await app.run_build(app.progress_queue.put_nowait, notices.append)
        except Exception as error:  # прерывание и сбои показываются отчётом
            self._drain()
            app.report_text = self._interrupted_report(error)
            app.interrupted = True
            self.app.push_screen(ReportScreen())
            return
        app.report_text = recorder.render()
        self._drain()
        self.app.push_screen(ReportScreen())

    def _interrupted_report(self, error: Exception) -> str:
        """Отчёт при прерывании: главное — сколько глав уже готово (14.10)."""
        built = self.last.done
        total = self.last.total or "?"
        return (
            f"Сборка прервана: {error}\n"
            f"  собрано глав: {built} из {total}\n"
            "  частичный EPUB можно сохранить."
        )

    def _drain(self) -> None:
        updated = False
        while not self.app.progress_queue.empty():
            self.last = self.app.progress_queue.get_nowait()
            updated = True
        if updated:
            self._apply(self.last)

    def _apply(self, progress: Progress) -> None:
        self.last = progress
        bar = self.query_one("#chapters", ProgressBar)
        bar.update(total=progress.total or 1, progress=progress.done)
        self.query_one("#chapter_info", Static).update(_chapter_line(progress))
        images = self.query_one("#images", ProgressBar)
        images.update(total=progress.images_total or 1, progress=progress.images_done)
        self.query_one("#images_info", Static).update(
            f"Изображения: {progress.images_done}/{progress.images_total or '?'}"
        )


class ReportScreen(Screen[None]):
    """Шаг 5: отчёт с путём, размером и потерями (14.8, 14.10)."""

    def compose(self) -> ComposeResult:
        app = cast(ExporterApp, self.app)
        with Vertical():
            yield Static("Отчёт", id="prompt")
            yield VerticalScroll(Static(app.report_text, id="report"))
            with Horizontal(classes="actions"):
                if app.interrupted and app.save_partial is not None:
                    yield Button("Сохранить частичный EPUB", id="save", variant="primary")
                yield Button("Выход", id="close")
            yield Static("", id="save_status")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app = cast(ExporterApp, self.app)
        if event.button.id == "close":
            app.exit()
        elif event.button.id == "save":
            self.run_worker(self._save(), exclusive=True)

    async def _save(self) -> None:
        app = cast(ExporterApp, self.app)
        if app.save_partial is None:
            return
        try:
            path = await app.save_partial()
        except Exception as error:
            self.query_one("#save_status", Static).update(f"Не удалось сохранить: {error}")
            return
        self.query_one("#save_status", Static).update(f"Частичный EPUB сохранён: {path}")


def _option(option_id: str, label: str) -> Option:
    return Option(label, id=option_id)


def _team_label(name: str, covered: int, total: int) -> str:
    return f"{name} — {covered} из {total} глав"


def _chapter_line(progress: Progress) -> str:
    if not progress.total:
        return "Главы: подготовка…"
    return (
        f"Главы: {progress.done}/{progress.total} ({progress.percent:.1f}%) · "
        f"{progress.speed:.2f} глав/с · осталось {_eta_text(progress.eta)}"
    )


def _eta_text(seconds: float) -> str:
    if seconds <= 0:
        return "0 с"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes} мин {secs} с" if minutes else f"{secs} с"


async def default_load_metadata(link: str, options: Options) -> Metadata:
    """Боевая загрузка метаданных: клиент создаётся и закрывается здесь же."""
    from ..cli.main import build_config
    from ..source.api import RanobeLibSource
    from ..source.client import RanobeLibClient
    from ..source.numbering import assign_labels, sort_chapters

    slug = parse_book_url(link)
    client = RanobeLibClient(build_config(options))
    try:
        source = RanobeLibSource(client)
        book = await source.fetch_book(slug)
        chapters = await source.fetch_chapters(slug)
    finally:
        await client.aclose()

    assign_labels(chapters)
    chapters = sort_chapters(chapters)
    selection = options.selection
    if selection is not None:
        chapters = selection.apply(chapters)
    return Metadata(book=book, chapters=chapters, slug=slug)


async def default_build(
    plan: BuildPlan, on_progress: ProgressSink, on_notice: NoticeSink
) -> ReportRecorder:
    """Боевая сборка: тот же код, что и в неинтерактивном режиме."""
    from ..cli.main import build_config, write_epub
    from ..images.pipeline import ImageAsset
    from ..pipeline.downloader import ChapterDownloader, ChapterTask
    from ..source.age import confirm_age, requires_confirmation
    from ..source.api import RanobeLibSource
    from ..source.client import RanobeLibClient
    from ..source.translations import apply_selection

    options = plan.options
    chapters = plan.chapters
    apply_selection(chapters, plan.team)

    recorder = ReportRecorder(total_chapters=len(chapters))
    coverage = compute_coverage(chapters, plan.team)
    if coverage.is_partial:
        on_notice(coverage_note(coverage))
    if requires_confirmation(plan.book):
        on_notice(confirm_age(plan.book, recorder.report))

    client = RanobeLibClient(build_config(options))
    try:
        source = RanobeLibSource(client)
        downloader = ChapterDownloader(
            source,
            recorder,
            on_progress=on_progress,
            max_image_mb=options.max_image_mb,
            max_image_width=options.max_image_width,
            quality=options.quality,
            include_images=options.include_images,
        )
        tasks = [
            ChapterTask(chapter, charset=options.charset, include_images=options.include_images)
            for chapter in chapters
        ]
        fetched = await downloader.fetch_all(tasks, book_slug=plan.slug)
        target = options.output_path_for(plan.book)
        target.parent.mkdir(parents=True, exist_ok=True)
        cover: ImageAsset | None = await downloader.fetch_cover(plan.book)
        write_epub(target, plan.book, fetched, options, recorder, cover=cover)
    finally:
        await client.aclose()

    return recorder


def run_tui(options: Options | None = None) -> None:
    """Запуск интерактивного режима по умолчанию."""
    ExporterApp(options).run()
