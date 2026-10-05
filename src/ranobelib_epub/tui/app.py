"""Интерактивный терминальный интерфейс на `textual` (решение 8, задачи 14.1-14.10).

Экран — это шаг сценария, а решения и данные живут на самом приложении, поэтому тесты
могут подменять загрузку метаданных и сборку, не поднимая сеть и не зависая на
терминале. Прогресс из сетевого слоя идёт через `asyncio.Queue`: сборка не обновляет
виджеты напрямую, а складывает снимки, которые интерфейс разбирает по таймеру. Так
event loop остаётся свободным, и окно перерисовывается даже при медленном сервере.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import ClassVar, cast

from PIL import Image
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Footer,
    Input,
    OptionList,
    ProgressBar,
    RichLog,
    Select,
    Static,
)
from textual.widgets.option_list import Option
from textual_image.widget import Image as ImageWidget

from ..cli.options import (
    ArgumentError,
    Options,
    apply_chapter_selection,
    build_options,
    iso_date,
    positive_int,
)
from ..images.presets import PRESETS, preset_names
from ..models import (
    LANGUAGE_CODES,
    LANGUAGE_LABELS,
    Book,
    Chapter,
    Cover,
    MetadataOverrides,
    apply_overrides,
    resolve_cover_url,
)
from ..numbering import assign_labels, sort_chapters
from ..pipeline.downloader import coverage_note
from ..pipeline.report import ChapterEvent, Progress, ReportRecorder
from ..sources import (
    BookSource,
    SourceError,
    create_source,
    resolve_source,
)
from ..translations import available_teams, compute_coverage

DEFAULT_OPTION = "__default__"
DEFAULT_LABEL = "Актуальная ветка (по умолчанию)"

MANUAL_PRESET = "__manual__"
MANUAL_PRESET_LABEL = "Вручную"

COVER_DEFAULT = "__cover_default__"
COVER_DEFAULT_LABEL = "Обложка книги (по умолчанию)"
COVER_NONE = "__cover_none__"
COVER_NONE_LABEL = "Без обложки"


def _preset_choices() -> list[tuple[str, str]]:
    """Варианты селектора пресетов: «Вручную» и имена из presets.py."""
    return [(MANUAL_PRESET_LABEL, MANUAL_PRESET)] + [
        (name, name) for name in preset_names()
    ]


@dataclass(slots=True)
class Metadata:
    """Книга и её главы, полученные до выбора перевода.

    `source` — открытый источник книги, из которого пришли данные: им пользуются
    превью обложек и сборка, чтобы не создавать клиент заново.
    """

    book: Book
    chapters: list[Chapter]
    slug: str = ""
    covers: tuple[Cover, ...] = ()
    source: BookSource | None = None


@dataclass(slots=True)
class BuildPlan:
    """Все решения пользователя, необходимые для сборки."""

    book: Book
    chapters: list[Chapter]
    options: Options
    slug: str
    team: str | None = None
    overrides: MetadataOverrides = field(default_factory=MetadataOverrides)
    cover_id: int | None = None
    cover_disabled: bool = False
    covers: tuple[Cover, ...] = ()
    source: BookSource | None = None


ProgressSink = Callable[[Progress], None]
NoticeSink = Callable[[str], None]
BuildFunction = Callable[[BuildPlan, ProgressSink, NoticeSink], Awaitable[ReportRecorder]]
LoadFunction = Callable[[str, Options], Awaitable[Metadata]]
SaveFunction = Callable[[], Awaitable[Path]]
PreviewFetch = Callable[[str | None], Awaitable[Image.Image | None]]
SupportsImages = Callable[[], bool]


class ExporterApp(App[None]):
    """Приложение: хранит состояние сценария и переключает экраны."""

    CSS = """
    $primary: #7aa2f7;
    $accent: #2ac3de;
    $surface: #1a1b26;
    $panel: #1f2335;
    $text: #c0caf5;
    $text-muted: #565f89;
    $error: #f7768e;
    $warning: #e0af68;

    Screen { padding: 1 2; background: $background; color: $text; }
    .screen-title { text-style: bold; color: $text; padding-bottom: 1; }
    #prompt { padding-bottom: 1; }
    .section { color: $text-muted; text-style: bold; padding-top: 1; padding-bottom: 1; }
    .field_label { padding-top: 1; color: $text-muted; }
    .error { color: $error; }
    .notice { color: $warning; }
    .card { border: round $border; padding: 1; }
    .actions { height: auto; padding-top: 1; }
    .actions Button { margin-right: 2; }
    #form { height: auto; }
    .preset_row { height: auto; }
    .preset_row .field_label { padding-top: 0; width: auto; }
    .preset_row Select { width: 1fr; }
    .column { width: 1fr; height: auto; padding-right: 2; }
    .column Input { width: 1fr; }
    #details_box { height: auto; }
    #report { height: 1fr; }
    #log_panel { height: auto; }
    #chapter_log { height: 12; }

    #cover { width: 1fr; }
    #main_row { height: auto; }
    #cover_preview { width: 40; height: 1fr; border: round $border; align: center middle; }
    #cover_image { width: 100%; height: auto; }
    #cover_placeholder { color: $text-muted; }
    """

    BINDINGS: ClassVar = [("q", "quit", "Выход")]

    def __init__(
        self,
        options: Options | None = None,
        *,
        load_metadata: LoadFunction | None = None,
        build: BuildFunction | None = None,
        save_partial: SaveFunction | None = None,
        fetch_preview: PreviewFetch | None = None,
        supports_terminal_images: SupportsImages | None = None,
    ) -> None:
        super().__init__()
        self.options = options or Options()
        self.metadata: Metadata | None = None
        self.team: str | None = None
        self.overrides: MetadataOverrides = MetadataOverrides()
        self.cover_id: int | None = None
        self.cover_disabled: bool = False
        self.metadata_draft: dict[str, str] = {}
        self.report_text: str = ""
        self.interrupted: bool = False
        self.progress_queue: asyncio.Queue[Progress] = asyncio.Queue()
        self.save_partial = save_partial
        self.preview_cache: dict[str, Image.Image] = {}
        self._preview_impl = fetch_preview
        self.supports_terminal_images = supports_terminal_images or default_terminal_supports_images
        self._load = load_metadata or default_load_metadata
        self._build = build or default_build

    def on_mount(self) -> None:
        self.push_screen(LinkScreen())

    async def on_unmount(self) -> None:
        """Закрывает источник книги, открытый при загрузке метаданных."""
        if self.metadata is not None and self.metadata.source is not None:
            await self.metadata.source.aclose()

    async def fetch_preview(self, url: str | None) -> Image.Image | None:
        """Превью обложки: подменённая реализация либо загрузка через источник."""
        if self._preview_impl is not None:
            return await self._preview_impl(url)
        return await self._fetch_preview(url)

    async def _fetch_preview(self, url: str | None) -> Image.Image | None:
        """Загружает изображение через источник книги, не блокируя выбор обложки."""
        if not url:
            return None
        source = self.metadata.source if self.metadata is not None else None
        if source is None:
            return None
        absolute = source.resource_url(url)
        if not absolute:
            return None
        try:
            raw = await source.fetch_resource(absolute)
        except Exception:
            return None
        try:
            with Image.open(io.BytesIO(raw)) as opened:
                return opened.copy()
        except Exception:
            return None

    async def load_metadata(self, link: str, options: Options | None = None) -> Metadata:
        metadata = await self._load(link, options or self.options)
        self.metadata = metadata
        return metadata

    async def run_build(self, on_progress: ProgressSink, on_notice: NoticeSink) -> ReportRecorder:
        if self.metadata is None:
            raise RuntimeError("метаданные не загружены")
        chapters = apply_chapter_selection(self.options, self.metadata.chapters)
        plan = BuildPlan(
            book=self.metadata.book,
            chapters=chapters,
            options=self.options,
            slug=self.metadata.slug,
            team=self.team,
            overrides=self.overrides,
            cover_id=self.cover_id,
            cover_disabled=self.cover_disabled,
            covers=self.metadata.covers,
            source=self.metadata.source,
        )
        return await self._build(plan, on_progress, on_notice)


class LinkScreen(Screen[None]):
    """Шаг 1: ссылка на книгу с проверкой без сети (14.1)."""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Шаг 1 · Ссылка на книгу", id="prompt", classes="screen-title")
            yield Input(
                placeholder="https://ranobelib.me/book/94231--...",
                id="link",
            )
            with Horizontal(classes="actions"):
                yield Button("Дальше", id="submit", variant="primary")
            yield Static("", id="error", classes="error")
        yield Footer()

    def on_mount(self) -> None:
        link = self.query_one("#link", Input)
        preset = cast(ExporterApp, self.app).options.slug_url or ""
        if preset:
            link.value = preset
        link.focus()

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
            resolve_source(link).parse(link)
        except SourceError as error:
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
            self.app.push_screen(MetadataScreen())

    @staticmethod
    def _has_choice(chapters: list[Chapter]) -> bool:
        """Есть ли у книги больше одной ветки (14.4)."""
        return len(available_teams(chapters)) > 1


class TranslationScreen(Screen[None]):
    """Шаг 2: выбор перевода с покрытием и предупреждением (14.2, 14.3, 14.5)."""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Шаг 2 · Выбор перевода", id="prompt", classes="screen-title")
            yield OptionList(id="teams")
            yield Static("", id="coverage")
            yield Static("", id="warning", classes="notice")
            with Horizontal(classes="actions"):
                yield Button("Продолжить", id="continue", variant="primary")
                yield Button("Показать непокрытые", id="details")
                yield Button("Назад", id="back")
            yield VerticalScroll(
                Static("", id="details_text"), id="details_box", classes="card"
            )
        yield Footer()

    def on_mount(self) -> None:
        app = cast(ExporterApp, self.app)
        teams = available_teams(app.metadata.chapters) if app.metadata else ()
        options = [_option(DEFAULT_OPTION, DEFAULT_LABEL)]
        options.extend(
            _option(team.name, _team_label(team.name, team.covered, team.total)) for team in teams
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
            app.push_screen(MetadataScreen())
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


class MetadataScreen(Screen[None]):
    """Шаг 3: редактирование метаданных EPUB и выбор обложки (решение 3 design.md).

    Поля предзаполнены значениями с сайта; пустое поле означает «использовать значение
    с сайта». Введённое хранится в `ExporterApp.metadata_draft`, поэтому возврат назад
    не теряет ввод (задача 6.2); валидированные результаты уходят в `overrides` и
    `cover_id`/`cover_disabled`.
    """

    _FIELDS = ("title", "author", "description", "genres", "date", "publisher", "series",
               "series_index")
    _SELECTS = ("language", "cover")

    def compose(self) -> ComposeResult:
        app = cast(ExporterApp, self.app)
        book = app.metadata.book if app.metadata else None
        with Vertical():
            yield Static("Шаг 3 · Метаданные и обложка", id="prompt", classes="screen-title")
            with VerticalScroll(), Horizontal(id="main_row"):
                with Vertical(classes="column"):
                    yield Static("Обложка", classes="section")
                    yield Select(
                        self._cover_choices(app),
                        value=self._cover_value(app),
                        allow_blank=False,
                        compact=True,
                        id="cover",
                    )
                    yield Static("Основное", classes="section")
                    yield Static("Название", classes="field_label")
                    yield Input(
                        value=self._value(app, "title", book.title if book else ""),
                        id="title",
                        compact=True,
                    )
                    yield Static("Автор", classes="field_label")
                    yield Input(
                        value=self._value(app, "author", (book.author if book else "") or ""),
                        id="author",
                        compact=True,
                    )
                    yield Static("Описание", classes="field_label")
                    yield Input(
                        value=self._value(
                            app, "description", (book.summary if book else "") or ""
                        ),
                        id="description",
                        compact=True,
                    )
                    yield Static("Жанры (через запятую)", classes="field_label")
                    yield Input(
                        value=self._value(
                            app, "genres", ", ".join(book.genres) if book else ""
                        ),
                        id="genres",
                        compact=True,
                    )
                    yield Static("Язык", classes="field_label")
                    yield Select(
                        _language_choices(),
                        value=self._language_value(app, book),
                        allow_blank=False,
                        compact=True,
                        id="language",
                    )
                    yield Static("Издание", classes="section")
                    yield Static("Дата (YYYY-MM-DD)", classes="field_label")
                    yield Input(
                        value=self._value(app, "date", ""),
                        id="date",
                        compact=True,
                    )
                    yield Static("Издатель", classes="field_label")
                    yield Input(
                        value=self._value(app, "publisher", ""),
                        id="publisher",
                        compact=True,
                    )
                    yield Static("Серия", classes="field_label")
                    yield Input(
                        value=self._value(app, "series", ""),
                        id="series",
                        compact=True,
                    )
                    yield Static("Номер тома", classes="field_label")
                    yield Input(
                        value=self._value(app, "series_index", ""),
                        id="series_index",
                        compact=True,
                    )
                with Vertical(id="cover_preview", classes="card"):
                    yield Static("", id="cover_placeholder")
                    yield ImageWidget(id="cover_image")
            yield Static("", id="form_error", classes="error")
            with Horizontal(classes="actions"):
                yield Button("Дальше", id="next", variant="primary")
                yield Button("Назад", id="back")
        yield Footer()

    @staticmethod
    def _value(app: ExporterApp, name: str, fallback: str) -> str:
        """Значение поля: черновик, затем флаг CLI, затем значение с сайта."""
        draft = app.metadata_draft.get(name)
        if draft is not None:
            return draft
        options_field = {"genres": "subjects"}.get(name, name)
        flag = getattr(app.options, options_field, None)
        if flag is not None:
            return str(flag)
        return fallback or ""

    @staticmethod
    def _language_value(app: ExporterApp, book: Book | None) -> str:
        draft = app.metadata_draft.get("language")
        if draft is not None:
            return draft
        if app.options.language:
            return app.options.language
        site = book.in_language if book else None
        return site if site in LANGUAGE_CODES else "ru"

    def _cover_choices(self, app: ExporterApp) -> list[tuple[str, str]]:
        choices = [(COVER_DEFAULT_LABEL, COVER_DEFAULT), (COVER_NONE_LABEL, COVER_NONE)]
        covers = app.metadata.covers if app.metadata else ()
        choices.extend((cover.label, str(cover.id)) for cover in covers)
        return choices

    def _cover_value(self, app: ExporterApp) -> str:
        draft = app.metadata_draft.get("cover")
        if draft is not None:
            if draft in (COVER_DEFAULT, COVER_NONE):
                return draft
            covers = app.metadata.covers if app.metadata else ()
            if any(cover.id == int(draft) for cover in covers):
                return draft
        if app.cover_disabled:
            return COVER_NONE
        if app.cover_id is not None:
            covers = app.metadata.covers if app.metadata else ()
            if any(cover.id == app.cover_id for cover in covers):
                return str(app.cover_id)
        return COVER_DEFAULT

    def _collect_all(self) -> None:
        """Синхронизирует черновик с текущими значениями виджетов (перед переходом)."""
        app = cast(ExporterApp, self.app)
        for name in self._FIELDS:
            app.metadata_draft[name] = self.query_one(f"#{name}", Input).value
        for select_id in self._SELECTS:
            app.metadata_draft[select_id] = str(self.query_one(f"#{select_id}", Select).value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self._collect_all()
            self.app.pop_screen()
            return
        if event.button.id != "next":
            return
        self._submit()

    def _submit(self) -> None:
        app = cast(ExporterApp, self.app)
        self._collect_all()
        query = self.query_one

        date_raw = query("#date", Input).value.strip()
        series_index_raw = query("#series_index", Input).value.strip()
        try:
            date_value = iso_date(date_raw) if date_raw else None
            series_index = positive_int(series_index_raw) if series_index_raw else None
        except ArgumentError as error:
            query("#form_error", Static).update(f"Ошибка: {error}")
            return
        query("#form_error", Static).update("")

        genres_raw = query("#genres", Input).value.strip()
        genres = tuple(part.strip() for part in genres_raw.split(",") if part.strip()) or None
        app.overrides = MetadataOverrides(
            title=query("#title", Input).value.strip() or None,
            author=query("#author", Input).value.strip() or None,
            description=query("#description", Input).value.strip() or None,
            language=str(query("#language", Select).value),
            genres=genres,
            date=date_value,
            publisher=query("#publisher", Input).value.strip() or None,
            series=query("#series", Input).value.strip() or None,
            series_index=series_index,
        )

        selection = str(query("#cover", Select).value)
        if selection == COVER_NONE:
            app.cover_disabled = True
            app.cover_id = None
        elif selection == COVER_DEFAULT:
            app.cover_disabled = False
            app.cover_id = None
        else:
            app.cover_disabled = False
            app.cover_id = int(selection)

        app.push_screen(ConfirmScreen())

    def on_mount(self) -> None:
        self._apply_cover_layout(self.app.size.width)
        self._refresh_preview()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "cover":
            self._refresh_preview()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_cover_layout(event.size.width)

    def _apply_cover_layout(self, width: int) -> None:
        """Раскладка формы: превью-панель справа от всех полей на широком терминале.

        Текстовые медиа-запросы (`@media`) Textual 8 не поддерживает, поэтому порог
        переноса обрабатывается по размеру экрана (решение 3 design.md). Панель превью
        всегда сохраняет пропорции картинки: ширина фиксирована CSS, высота следует за
        соотношением сторон (`height: auto`) либо растягивается на всю высоту формы
        (`height: 1fr`) в горизонтальной раскладке.
        """
        try:
            row = self.query_one("#main_row")
            preview = self.query_one("#cover_preview")
        except NoMatches:
            return
        if width < 100:
            row.styles.layout = "vertical"
            preview.styles.height = "auto"
        else:
            row.styles.layout = "horizontal"
            preview.styles.height = "1fr"

    def _cover_selection_url(self) -> str | None:
        """URL выбранной обложки: по умолчанию — с сайта, том — из карусели, иначе нет."""
        app = cast(ExporterApp, self.app)
        selection = str(self.query_one("#cover", Select).value)
        if selection == COVER_NONE:
            return None
        if selection == COVER_DEFAULT:
            return app.metadata.book.cover if app.metadata else None
        covers = app.metadata.covers if app.metadata else ()
        for cover in covers:
            if cover.id == int(selection):
                return cover.url
        return None

    def _refresh_preview(self) -> None:
        """Показывает превью для текущего выбора обложки: кэш, загрузка или скрытие."""
        app = cast(ExporterApp, self.app)
        url = self._cover_selection_url()
        preview = self.query_one("#cover_preview", Vertical)
        if url is None:
            preview.display = False
            return
        preview.display = True
        if not app.supports_terminal_images():
            self._show_placeholder("Ваш терминал не поддерживает изображения")
            return
        cached = app.preview_cache.get(url)
        if cached is not None:
            self._show_image(cached)
            return
        self._show_placeholder("Загрузка…")
        self.run_worker(self._load_preview(url), group="cover_preview")

    async def _load_preview(self, url: str) -> None:
        app = cast(ExporterApp, self.app)
        try:
            image = await app.fetch_preview(url)
        except Exception:
            image = None
        try:
            if not self.is_mounted:
                return
            if image is None:
                self._show_placeholder("Нет превью")
                return
            app.preview_cache[url] = image
            self._show_image(image)
        except NoMatches:
            return

    def _show_image(self, image: Image.Image) -> None:
        widget = self.query_one("#cover_image", ImageWidget)
        widget.image = image
        widget.display = True
        self.query_one("#cover_placeholder", Static).display = False

    def _show_placeholder(self, text: str) -> None:
        self.query_one("#cover_image", ImageWidget).display = False
        placeholder = self.query_one("#cover_placeholder", Static)
        placeholder.update(text)
        placeholder.display = True


def _language_choices() -> list[tuple[str, str]]:
    """Варианты селектора языка: метка + код из набора 10 языков."""
    return [(f"{LANGUAGE_LABELS.get(code, code)} ({code})", code) for code in LANGUAGE_CODES]


class ConfirmScreen(Screen[None]):
    """Шаг 3: редактируемые параметры выгрузки и запуск сборки (3.1–3.4)."""

    def compose(self) -> ComposeResult:
        app = cast(ExporterApp, self.app)
        options = app.options
        book = app.metadata.book if app.metadata else None
        chapters = app.metadata.chapters if app.metadata else []
        header = (
            f"Книга: {book.title if book else '—'} · глав: {len(chapters)} · "
            f"перевод: {app.team or DEFAULT_LABEL}"
        )
        with Vertical():
            yield Static("Шаг 4 · Параметры выгрузки", id="prompt", classes="screen-title")
            yield Static(header, id="summary")
            yield Static("Параметры выгрузки", id="form_title")
            with Horizontal(id="form"):
                with Vertical(classes="column"):
                    with Horizontal(classes="preset_row"):
                        yield Static("Пресет сжатия", classes="field_label")
                        yield Select(
                            _preset_choices(),
                            value=options.preset or MANUAL_PRESET,
                            allow_blank=False,
                            compact=True,
                            id="preset",
                        )
                    yield Static("Лимит запросов в секунду", classes="field_label")
                    yield Input(value=str(options.rate_limit), id="rate_limit", compact=True)
                    yield Static("Число повторов", classes="field_label")
                    yield Input(value=str(options.retries), id="retries", compact=True)
                    yield Static("Порог сжатия, МБ (0 — без сжатия)", classes="field_label")
                    yield Input(value=str(options.max_image_mb), id="max_image_mb", compact=True)
                    yield Static("Максимальная ширина, px", classes="field_label")
                    yield Input(
                        value=str(options.max_image_width), id="max_image_width", compact=True
                    )
                with Vertical(classes="column"):
                    yield Static("Качество JPEG (1–100)", classes="field_label")
                    yield Input(value=str(options.quality), id="quality", compact=True)
                    yield Static("Путь к файлу (пусто — имя по умолчанию)", classes="field_label")
                    yield Input(value=str(options.output or ""), id="output", compact=True)
                    yield Static("Выборка глав (пусто — все)", classes="field_label")
                    yield Input(value=options.chapters or "", id="chapters", compact=True)
                    yield Checkbox(
                        "Скачивать изображения",
                        value=options.include_images,
                        id="include_images",
                    )
            yield Static("", id="form_error", classes="error")
            with Horizontal(classes="actions"):
                yield Button("Начать", id="start", variant="primary")
                yield Button("Отмена", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        app = cast(ExporterApp, self.app)
        self._apply_preset(app.options.preset or MANUAL_PRESET)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "preset":
            self._apply_preset(str(event.value))

    def _apply_preset(self, value: str) -> None:
        """Заполняет и блокирует ручные поля для пресета либо разблокирует их."""
        fields = ("#max_image_mb", "#max_image_width", "#quality")
        if value == MANUAL_PRESET:
            for selector in fields:
                self.query_one(selector, Input).disabled = False
            return
        preset = PRESETS.get(value)
        if preset is None:
            return
        self.query_one("#max_image_mb", Input).value = str(preset.max_mb)
        self.query_one("#max_image_width", Input).value = str(preset.max_width)
        self.query_one("#quality", Input).value = str(preset.quality)
        for selector in fields:
            self.query_one(selector, Input).disabled = True

    def _form_options(self) -> Options:
        app = cast(ExporterApp, self.app)
        selection = self.query_one("#preset", Select).value
        preset = None if selection == MANUAL_PRESET else str(selection)
        return build_options(
            app.options,
            rate_limit=self.query_one("#rate_limit", Input).value,
            retries=self.query_one("#retries", Input).value,
            include_images=self.query_one("#include_images", Checkbox).value,
            max_image_mb=self.query_one("#max_image_mb", Input).value,
            max_image_width=self.query_one("#max_image_width", Input).value,
            quality=self.query_one("#quality", Input).value,
            output=self.query_one("#output", Input).value,
            chapters=self.query_one("#chapters", Input).value,
            preset=preset,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.app.exit()
            return
        if event.button.id != "start":
            return
        app = cast(ExporterApp, self.app)
        try:
            options = self._form_options()
        except ArgumentError as error:
            self.query_one("#form_error", Static).update(f"Ошибка: {error}")
            return
        self.query_one("#form_error", Static).update("")
        app.options = options
        self.app.push_screen(ProgressScreen())


class ProgressScreen(Screen[None]):
    """Шаг 4: ход сборки, отдельный индикатор картинок (14.6, 14.7)."""

    BINDINGS: ClassVar = [("l", "toggle_log", "Журнал")]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Шаг 5 · Сборка книги", id="prompt", classes="screen-title")
            yield ProgressBar(total=100, id="chapters")
            yield Static("", id="chapter_info")
            yield ProgressBar(total=100, id="images")
            yield Static("", id="images_info")
            with Collapsible(title="Журнал глав", collapsed=True, id="log_panel"):
                yield RichLog(id="chapter_log", max_lines=5000, auto_scroll=False)
            yield Static("", id="notices", classes="notice")
        yield Footer()

    def on_mount(self) -> None:
        self.last: Progress = Progress()
        self.set_interval(0.05, self._drain)
        self.run_worker(self._run(), exclusive=True, name="build")

    def action_toggle_log(self) -> None:
        """Свернуть или развернуть панель журнала, не прерывая сборку (решение 5)."""
        panel = self.query_one("#log_panel", Collapsible)
        panel.collapsed = not panel.collapsed

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
        while not self.app.progress_queue.empty():
            self._apply(self.app.progress_queue.get_nowait())

    def _apply(self, progress: Progress) -> None:
        self.last = progress
        bar = self.query_one("#chapters", ProgressBar)
        done = progress.fetched if progress.downloading else progress.done
        bar.update(total=progress.total or 1, progress=done)
        self.query_one("#chapter_info", Static).update(_chapter_line(progress))
        images = self.query_one("#images", ProgressBar)
        images.update(total=progress.images_total or 1, progress=progress.images_done)
        self.query_one("#images_info", Static).update(
            f"Изображения: {progress.images_done}/{progress.images_total or '?'}"
        )
        if progress.last_event is not None:
            self._write_event(progress.last_event)

    def _write_event(self, event: ChapterEvent) -> None:
        """Дописывает строку журнала, не прокручивая панель принудительно (решение 5)."""
        self.query_one("#chapter_log", RichLog).write(event.render())


class ReportScreen(Screen[None]):
    """Шаг 5: отчёт с путём, размером и потерями (14.8, 14.10)."""

    def compose(self) -> ComposeResult:
        app = cast(ExporterApp, self.app)
        with Vertical():
            yield Static("Шаг 6 · Отчёт", id="prompt", classes="screen-title")
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
    if progress.downloading:
        return (
            f"Скачивание глав: {progress.fetched}/{progress.total} "
            f"({progress.fetch_percent:.1f}%) · прошло {_eta_text(progress.elapsed)}"
        )
    return (
        f"Главы: {progress.done}/{progress.total} ({progress.percent:.1f}%) · "
        f"{progress.speed:.2f} глав/с · осталось {_eta_text(progress.eta)}"
    )


def _eta_text(seconds: float) -> str:
    if seconds <= 0:
        return "0 с"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes} мин {secs} с" if minutes else f"{secs} с"


def default_terminal_supports_images() -> bool:
    """Терминал умеет рисовать настоящие изображения (TGP или Sixel).

    Иначе `textual-image` рисует суррогат (half-cell/Unicode), а по требованию
    «Запасной вариант без поддержки изображений» вместо картинки показывается текст.
    """
    from textual_image.widget import AutoRenderable, SixelRenderable, TGPRenderable

    return AutoRenderable is SixelRenderable or AutoRenderable is TGPRenderable


async def default_load_metadata(link: str, options: Options) -> Metadata:
    """Боевая загрузка метаданных: открытый источник сохраняется в `Metadata`.

    Источник не закрывается здесь: им пользуются превью обложек и сборка, а закрывает
    его приложение при завершении работы.
    """
    from ..cli.main import build_config

    source = create_source(link, build_config(options))
    try:
        ref = source.parse_url(link)
        book = await source.fetch_book(ref)
        chapters = await source.fetch_chapters(ref)
        covers = await source.fetch_covers(ref)
    except Exception:
        await source.aclose()
        raise

    assign_labels(chapters)
    chapters = sort_chapters(chapters)
    return Metadata(book=book, chapters=chapters, slug=ref, covers=covers, source=source)


async def default_build(
    plan: BuildPlan, on_progress: ProgressSink, on_notice: NoticeSink
) -> ReportRecorder:
    """Боевая сборка: тот же код, что и в неинтерактивном режиме.

    Источник приходит в плане и принадлежит приложению, поэтому здесь он не закрывается.
    """
    from ..cli.main import write_epub
    from ..images.pipeline import ImageAsset
    from ..pipeline.downloader import ChapterDownloader, ChapterTask
    from ..translations import apply_selection

    source = plan.source
    if source is None:
        raise RuntimeError("источник книги не загружен")

    options = plan.options
    chapters = plan.chapters
    apply_selection(chapters, plan.team)

    recorder = ReportRecorder(total_chapters=len(chapters))
    coverage = compute_coverage(chapters, plan.team)
    if coverage.is_partial:
        on_notice(coverage_note(coverage))
    if source.requires_age_confirmation(plan.book):
        on_notice(source.confirm_age(plan.book, recorder.report))

    effective_book = apply_overrides(plan.book, plan.overrides)
    cover_url = resolve_cover_url(plan.book, plan.covers, plan.cover_id, plan.cover_disabled)
    if plan.cover_id is not None:
        chosen = next((item for item in plan.covers if item.id == plan.cover_id), None)
        if chosen is None or not chosen.url:
            recorder.report.notes.append(
                f"выбранная обложка {plan.cover_id} недоступна; EPUB собран без обложки"
            )

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
    fetched = await downloader.fetch_all(tasks, book_ref=plan.slug)
    target = options.output_path_for(effective_book)
    target.parent.mkdir(parents=True, exist_ok=True)
    build_date = None
    if plan.overrides.date:
        build_date = date.fromisoformat(plan.overrides.date)
    cover: ImageAsset | None = await downloader.fetch_cover(cover_url)
    write_epub(
        target,
        effective_book,
        fetched,
        options,
        recorder,
        cover=cover,
        build_date=build_date,
    )

    return recorder


def run_tui(options: Options | None = None) -> None:
    """Запуск интерактивного режима по умолчанию."""
    ExporterApp(options).run()
