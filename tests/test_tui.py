"""Тесты интерактивного TUI (задачи 14.1-14.10).

Сеть не поднимается: экраны получают подменённые загрузку метаданных и сборку, а
взаимодействие идёт через `App.run_test()` — тот же рендеринг, что и в терминале.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PIL import Image as PILImage
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Input,
    OptionList,
    ProgressBar,
    RichLog,
    Select,
    Static,
)
from textual_image.widget import Image as ImageWidget
from textual_image.widget import get_cell_size

from ranobelib_epub.cli.options import Options
from ranobelib_epub.models import Book, Chapter, Cover, TranslationBranch
from ranobelib_epub.pipeline.report import ChapterEvent, Progress, ReportRecorder
from ranobelib_epub.tui.app import (
    COVER_DEFAULT,
    COVER_DEFAULT_LABEL,
    COVER_NONE,
    COVER_NONE_LABEL,
    DEFAULT_OPTION,
    MANUAL_PRESET,
    ConfirmScreen,
    ExporterApp,
    LinkScreen,
    Metadata,
    MetadataScreen,
    ProgressScreen,
    ReportScreen,
    TranslationScreen,
    default_load_metadata,
)

LINK = "https://ranobelib.me/book/94231--rezero"
BOOK = Book(slug_url="94231--rezero", rus_name="Re:Zero", name="Re:Zero")
RICH_BOOK = Book(
    slug_url="94231--rezero",
    rus_name="Re:Zero",
    name="Re:Zero",
    author="Tappei Nagatsuki",
    summary="Возвращение домой из магазина.",
    genres=("Драма", "Фэнтези"),
)
COVERS = (
    Cover(id=18021734, order=0, label="Том 1", url="https://cover/x1.jpg"),
    Cover(id=18021735, order=1, label="Том 2", url="https://cover/x2.jpg"),
)
COVER_BOOK = Book(
    slug_url="94231--rezero", rus_name="Re:Zero", cover="https://book/default.jpg"
)


def tiny_image() -> PILImage.Image:
    """Миниатюра для заглушки превью: не требует сети и реальных пикселей."""
    return PILImage.new("RGB", (60, 90), color=(120, 140, 180))


def make_preview(images: dict[str, PILImage.Image] | None = None):
    """Заглушка `fetch_preview`: запоминает вызовы и отдаёт изображения по URL."""
    calls: list[str | None] = []
    images = images or {}

    async def load(url: str | None) -> PILImage.Image | None:
        calls.append(url)
        return images.get(url)

    return load, calls


async def no_preview(url: str | None) -> PILImage.Image | None:
    """Заглушка без сети для тестов, которые превью не проверяют."""
    return None


def terminal_supports_images() -> bool:
    """Заглушка: тесты считают, что терминал умеет рисовать изображения."""
    return True


def make_chapter(index: int, teams: tuple[str, ...]) -> Chapter:
    branches = tuple(
        TranslationBranch(id=1000 + index * 10 + position, teams=(team,))
        for position, team in enumerate(teams)
    )
    return Chapter(
        id=index,
        volume=1,
        number=str(index),
        name=f"Глава {index}",
        branches=branches,
        label=f"1.{index}",
    )


def multi_team_chapters() -> list[Chapter]:
    """Alpha везде, Beta — на главах 1..5, Gamma — на 1..3."""
    chapters = []
    for index in range(1, 11):
        teams = ["Alpha"]
        if index <= 5:
            teams.append("Beta")
        if index <= 3:
            teams.append("Gamma")
        chapters.append(make_chapter(index, tuple(teams)))
    return chapters


def single_team_chapters() -> list[Chapter]:
    return [make_chapter(index, ("Only",)) for index in range(1, 6)]


def metadata(chapters: list[Chapter], covers: tuple[Cover, ...] = ()) -> Metadata:
    return Metadata(book=BOOK, chapters=chapters, slug="94231--rezero", covers=covers)


def loader(chapters: list[Chapter], covers: tuple[Cover, ...] = ()):
    async def load(link: str, options) -> Metadata:
        return metadata(chapters, covers)

    return load


def fast_build():
    async def build(plan, on_progress, on_notice):
        recorder = ReportRecorder(total_chapters=len(plan.chapters))
        for index in range(1, len(plan.chapters) + 1):
            on_progress(
                Progress(
                    done=index,
                    total=len(plan.chapters),
                    elapsed=float(index),
                    images_done=index,
                    images_total=len(plan.chapters),
                )
            )
            recorder.chapter_built()
            await asyncio.sleep(0)
        recorder.finished("/tmp/book.epub", 2_000_000)
        return recorder

    return build


class GatedBuild:
    """Сборка, которая ждёт сигнала: даёт проверить прогресс и отзывчивость UI."""

    def __init__(self, total: int = 10, prompts: int = 3) -> None:
        self.total = total
        self.prompts = prompts
        self.gate = asyncio.Event()

    async def __call__(self, plan, on_progress, on_notice):
        for index in range(1, self.prompts + 1):
            on_progress(
                Progress(
                    done=index,
                    total=self.total,
                    elapsed=float(index),
                    images_done=index,
                    images_total=self.prompts,
                )
            )
            await asyncio.sleep(0)
        await self.gate.wait()
        recorder = ReportRecorder(total_chapters=self.total)
        for _ in range(self.total):
            recorder.chapter_built()
        recorder.finished("/tmp/book.epub", 2048)
        return recorder


class JournalBuild:
    """Сборка с событиями глав: наполняет журнал, затем ждёт сигнала."""

    def __init__(self, total: int = 5) -> None:
        self.total = total
        self.gate = asyncio.Event()

    async def __call__(self, plan, on_progress, on_notice):
        recorder = ReportRecorder(total_chapters=self.total)
        for index in range(1, self.total + 1):
            built = index != 2
            chapter = plan.chapters[index - 1]
            if built:
                recorder.chapter_built()
            else:
                recorder.chapter_unavailable(chapter, "таймаут запроса")
            event = ChapterEvent(
                position=index,
                total=self.total,
                label=f"1.{index}",
                name=f"Глава {index}",
                built=built,
                reason=None if built else "таймаут запроса",
                images=1 if built else 0,
            )
            on_progress(
                Progress(
                    done=index if built else index - 1,
                    total=self.total,
                    elapsed=float(index),
                    last_event=event,
                )
            )
            await asyncio.sleep(0)
        await self.gate.wait()
        recorder.finished("/tmp/book.epub", 2048)
        return recorder


def interrupting_build(progress_calls: int = 1):
    async def build(plan, on_progress, on_notice):
        for _ in range(progress_calls):
            on_progress(Progress(done=4, total=10, elapsed=4.0))
            await asyncio.sleep(0)
        raise RuntimeError("сеть недоступна")

    return build


async def submit_link(pilot, app: ExporterApp, link: str = LINK) -> None:
    await pilot.pause()
    app.screen.query_one("#link", Input).value = link
    await pilot.click("#submit")
    await app.workers.wait_for_complete()
    await pilot.pause()


async def enter_metadata(pilot, app: ExporterApp) -> None:
    await submit_link(pilot, app)
    await pilot.click("#next")
    await pilot.pause()


async def enter_confirm(pilot, app: ExporterApp) -> None:
    await enter_metadata(pilot, app)
    await pilot.click("#start")
    await pilot.pause()


class TestLinkScreen:
    async def test_link_is_prefilled_from_options(self) -> None:
        from ranobelib_epub.cli.options import parse_args

        options = parse_args([LINK])
        app = ExporterApp(load_metadata=loader(single_team_chapters()), options=options)

        async with app.run_test() as pilot:
            await pilot.pause()

            assert isinstance(app.screen, LinkScreen)
            assert app.screen.query_one("#link", Input).value == LINK

    async def test_invalid_link_shows_error_and_stays(self) -> None:
        app = ExporterApp(load_metadata=loader(multi_team_chapters()))

        async with app.run_test() as pilot:
            await submit_link(pilot, app, "https://ranobelib.me/")

            assert isinstance(app.screen, LinkScreen)
            assert "не указывает на книгу" in str(app.screen.query_one("#error", Static).content)

    async def test_valid_link_moves_to_translation(self) -> None:
        app = ExporterApp(load_metadata=loader(multi_team_chapters()))

        async with app.run_test() as pilot:
            await submit_link(pilot, app)

            assert isinstance(app.screen, TranslationScreen)
            assert app.metadata is not None
            assert app.metadata.book.title == "Re:Zero"

    async def test_load_failure_stays_on_link(self) -> None:
        async def failing(link: str, options) -> Metadata:
            raise RuntimeError("книга не найдена")

        app = ExporterApp(load_metadata=failing)

        async with app.run_test() as pilot:
            await submit_link(pilot, app)

            assert isinstance(app.screen, LinkScreen)
            assert "не найдена" in str(app.screen.query_one("#error", Static).content)

    async def test_single_branch_skips_translation(self) -> None:
        app = ExporterApp(load_metadata=loader(single_team_chapters()))

        async with app.run_test() as pilot:
            await submit_link(pilot, app)

            assert isinstance(app.screen, MetadataScreen)


class TestTranslationScreen:
    async def test_lists_teams_with_counts_and_default(self) -> None:
        app = ExporterApp(load_metadata=loader(multi_team_chapters()))

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            option_list = app.screen.query_one("#teams", OptionList)

            labels = [str(option_list.get_option_at_index(i).prompt) for i in range(4)]

            assert "актуальная" in labels[0].casefold()
            assert "Alpha — 10 из 10 глав" in labels
            assert "Beta — 5 из 10 глав" in labels
            assert option_list.get_option_at_index(0).id == DEFAULT_OPTION

    async def test_partial_selection_warns_before_download(self) -> None:
        app = ExporterApp(load_metadata=loader(multi_team_chapters()))

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            option_list = app.screen.query_one("#teams", OptionList)
            option_list.highlighted = 2  # Beta
            await pilot.pause()

            warning = str(app.screen.query_one("#warning", Static).content)

            assert "5 из 10" in warning

    async def test_continue_and_back_actions(self) -> None:
        app = ExporterApp(load_metadata=loader(multi_team_chapters()))

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            assert isinstance(app.screen, TranslationScreen)

            await pilot.click("#back")
            await pilot.pause()
            assert isinstance(app.screen, LinkScreen)

            await submit_link(pilot, app)
            await pilot.click("#continue")
            await pilot.pause()
            assert isinstance(app.screen, MetadataScreen)

    async def test_details_list_uncovered_numbers(self) -> None:
        app = ExporterApp(load_metadata=loader(multi_team_chapters()))

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            option_list = app.screen.query_one("#teams", OptionList)
            option_list.highlighted = 2  # Beta покрывает 1..5
            await pilot.pause()
            await pilot.click("#details")
            await pilot.pause()

            details = str(app.screen.query_one("#details_text", Static).content)

            assert "1.6" in details
            assert "1.10" in details
            assert "1.1" not in details.replace("1.10", "")

    async def test_full_default_coverage_has_no_warning(self) -> None:
        app = ExporterApp(load_metadata=loader(multi_team_chapters()))

        async with app.run_test() as pilot:
            await submit_link(pilot, app)

            assert str(app.screen.query_one("#warning", Static).content) == ""


def build_loader(book=RICH_BOOK, chapters=None, covers=()):
    if chapters is None:
        chapters = single_team_chapters()

    async def load(link: str, options) -> Metadata:
        return Metadata(book=book, chapters=chapters, slug="94231--rezero", covers=covers)

    return load


class TestMetadataScreen:
    async def test_prefilled_from_site_values(self) -> None:
        app = ExporterApp(load_metadata=build_loader())

        async with app.run_test(size=(80, 24)) as pilot:
            await submit_link(pilot, app)

            assert isinstance(app.screen, MetadataScreen)
            assert app.screen.query_one("#title", Input).value == "Re:Zero"
            assert app.screen.query_one("#author", Input).value == "Tappei Nagatsuki"
            assert app.screen.query_one("#description", Input).value == (
                "Возвращение домой из магазина."
            )
            assert app.screen.query_one("#genres", Input).value == "Драма, Фэнтези"
            assert app.screen.query_one("#language", Select).value == "ru"
            assert app.screen.query_one("#cover", Select).value == COVER_DEFAULT

    async def test_language_select_offers_ten_codes(self) -> None:
        app = ExporterApp(load_metadata=build_loader())

        async with app.run_test() as pilot:
            await submit_link(pilot, app)

            select = app.screen.query_one("#language", Select)
            options = {str(value) for _, value in select._options}
            assert options == {
                "ru", "en", "ja", "zh", "ko", "de", "fr", "es", "it", "pt",
            }

    async def test_cover_select_includes_default_none_and_volumes(self) -> None:
        app = ExporterApp(load_metadata=build_loader(covers=COVERS))

        async with app.run_test() as pilot:
            await submit_link(pilot, app)

            select = app.screen.query_one("#cover", Select)
            prompts = [str(prompt) for prompt, _ in select._options]
            assert COVER_DEFAULT_LABEL in prompts
            assert COVER_NONE_LABEL in prompts
            assert any("Том 1" in prompt for prompt in prompts)
            assert any("Том 2" in prompt for prompt in prompts)

    async def test_invalid_date_stays_with_error(self) -> None:
        app = ExporterApp(load_metadata=build_loader())

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            app.screen.query_one("#date", Input).value = "2020-13-40"
            await pilot.click("#next")
            await pilot.pause()

            assert isinstance(app.screen, MetadataScreen)
            assert "YYYY-MM-DD" in str(
                app.screen.query_one("#form_error", Static).content
            )

    async def test_invalid_series_index_stays_with_error(self) -> None:
        app = ExporterApp(load_metadata=build_loader())

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            app.screen.query_one("#series_index", Input).value = "abc"
            await pilot.click("#next")
            await pilot.pause()

            assert isinstance(app.screen, MetadataScreen)
            assert "Ошибка" in str(app.screen.query_one("#form_error", Static).content)

    async def test_edits_flow_into_build_plan(self) -> None:
        captured: dict = {}

        async def build(plan, on_progress, on_notice):
            captured["plan"] = plan
            recorder = ReportRecorder(total_chapters=len(plan.chapters))
            recorder.finished("/tmp/book.epub", 1)
            return recorder

        app = ExporterApp(
            load_metadata=build_loader(covers=COVERS),
            build=build,
            fetch_preview=no_preview,
        )

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            assert isinstance(app.screen, MetadataScreen)
            app.screen.query_one("#title", Input).value = "Моя книга"
            app.screen.query_one("#genres", Input).value = "Приключения"
            app.screen.query_one("#date", Input).value = "2024-05-01"
            app.screen.query_one("#series", Input).value = "Re:Zero"
            app.screen.query_one("#series_index", Input).value = "3"
            app.screen.query_one("#cover", Select).value = "18021735"
            await pilot.click("#next")
            await pilot.pause()
            await pilot.click("#start")
            await app.workers.wait_for_complete()
            await pilot.pause()

        plan = captured["plan"]
        assert plan.overrides.title == "Моя книга"
        assert plan.overrides.genres == ("Приключения",)
        assert plan.overrides.date == "2024-05-01"
        assert plan.overrides.series == "Re:Zero"
        assert plan.overrides.series_index == 3
        assert plan.cover_id == 18021735
        assert plan.cover_disabled is False

    async def test_back_preserves_entered_values(self) -> None:
        app = ExporterApp(load_metadata=build_loader())

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            assert isinstance(app.screen, MetadataScreen)
            app.screen.query_one("#title", Input).value = "Черновик"
            await pilot.click("#back")
            await pilot.pause()
            assert isinstance(app.screen, LinkScreen)

            await submit_link(pilot, app)
            assert isinstance(app.screen, MetadataScreen)
            assert app.screen.query_one("#title", Input).value == "Черновик"

    async def test_no_cover_and_cover_selection_reach_plan(self) -> None:
        captured: dict = {}

        async def build(plan, on_progress, on_notice):
            captured["plan"] = plan
            recorder = ReportRecorder(total_chapters=len(plan.chapters))
            recorder.finished("/tmp/book.epub", 1)
            return recorder

        app = ExporterApp(load_metadata=build_loader(covers=COVERS), build=build)

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            app.screen.query_one("#cover", Select).value = COVER_NONE
            await pilot.click("#next")
            await pilot.pause()
            await pilot.click("#start")
            await app.workers.wait_for_complete()
            await pilot.pause()

        plan = captured["plan"]
        assert plan.cover_disabled is True
        assert plan.cover_id is None


class TestCoverPreview:
    """Превью обложки на шаге «Метаданные и обложка» (решение 2–4 design.md)."""

    async def test_volume_cover_shows_preview_image(self) -> None:
        load, calls = make_preview({"https://cover/x1.jpg": tiny_image()})
        app = ExporterApp(
            load_metadata=build_loader(covers=COVERS),
            fetch_preview=load,
            supports_terminal_images=terminal_supports_images,
        )

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            assert isinstance(app.screen, MetadataScreen)
            assert app.screen.query_one("#cover_preview", Vertical).display is False

            app.screen.query_one("#cover", Select).value = "18021734"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            widget = app.screen.query_one("#cover_image", ImageWidget)
            assert widget.display is True
            assert isinstance(widget.image, PILImage.Image)
            assert calls == ["https://cover/x1.jpg"]

    async def test_default_cover_shows_site_cover(self) -> None:
        load, calls = make_preview({"https://book/default.jpg": tiny_image()})
        app = ExporterApp(
            load_metadata=build_loader(book=COVER_BOOK),
            fetch_preview=load,
            supports_terminal_images=terminal_supports_images,
        )

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            assert app.screen.query_one("#cover", Select).value == COVER_DEFAULT
            await app.workers.wait_for_complete()
            await pilot.pause()

            widget = app.screen.query_one("#cover_image", ImageWidget)
            assert widget.display is True
            assert calls == ["https://book/default.jpg"]

    async def test_no_cover_hides_preview(self) -> None:
        load, _ = make_preview({"https://cover/x1.jpg": tiny_image()})
        app = ExporterApp(
            load_metadata=build_loader(covers=COVERS),
            fetch_preview=load,
            supports_terminal_images=terminal_supports_images,
        )

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            select = app.screen.query_one("#cover", Select)
            select.value = "18021734"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.screen.query_one("#cover_image", ImageWidget).display is True

            select.value = COVER_NONE
            await pilot.pause()

            assert app.screen.query_one("#cover_preview", Vertical).display is False

    async def test_preview_failure_shows_placeholder(self) -> None:
        load, _ = make_preview({})
        app = ExporterApp(
            load_metadata=build_loader(covers=COVERS),
            fetch_preview=load,
            supports_terminal_images=terminal_supports_images,
        )

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            app.screen.query_one("#cover", Select).value = "18021734"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            placeholder = app.screen.query_one("#cover_placeholder", Static)
            assert placeholder.display is True
            assert "Нет превью" in str(placeholder.content)
            assert app.screen.query_one("#cover_image", ImageWidget).display is False

    async def test_preview_cached_per_url(self) -> None:
        load, calls = make_preview({"https://cover/x1.jpg": tiny_image()})
        app = ExporterApp(
            load_metadata=build_loader(covers=COVERS),
            fetch_preview=load,
            supports_terminal_images=terminal_supports_images,
        )

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            select = app.screen.query_one("#cover", Select)
            select.value = "18021734"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            select.value = "18021735"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            select.value = "18021734"
            await pilot.pause()

            assert calls == ["https://cover/x1.jpg", "https://cover/x2.jpg"]

    async def test_preview_preserves_aspect_ratio(self) -> None:
        load, _ = make_preview({"https://cover/x1.jpg": tiny_image()})
        app = ExporterApp(
            load_metadata=build_loader(covers=COVERS),
            fetch_preview=load,
            supports_terminal_images=terminal_supports_images,
        )

        async with app.run_test(size=(120, 40)) as pilot:
            await submit_link(pilot, app)
            app.screen.query_one("#cover", Select).value = "18021734"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            widget = app.screen.query_one("#cover_image", ImageWidget)
            assert widget.outer_size.width > 20
            cell = get_cell_size()
            rendered = (widget.outer_size.width * cell.width) / (
                widget.outer_size.height * cell.height
            )
            source = tiny_image().width / tiny_image().height
            assert rendered == pytest.approx(source, rel=0.05)

    async def test_terminal_without_image_support_shows_fallback_text(self) -> None:
        load, calls = make_preview({"https://cover/x1.jpg": tiny_image()})
        app = ExporterApp(
            load_metadata=build_loader(covers=COVERS),
            fetch_preview=load,
            supports_terminal_images=lambda: False,
        )

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            app.screen.query_one("#cover", Select).value = "18021734"
            await pilot.pause()

            placeholder = app.screen.query_one("#cover_placeholder", Static)
            assert placeholder.display is True
            assert "не поддерживает изображения" in str(placeholder.content)
            assert app.screen.query_one("#cover_image", ImageWidget).display is False
            assert calls == []

    async def test_cover_select_first_and_preview_shifts_all_fields(self) -> None:
        load, _ = make_preview({"https://book/default.jpg": tiny_image()})
        app = ExporterApp(load_metadata=build_loader(book=COVER_BOOK), fetch_preview=load)

        async with app.run_test() as pilot:
            await submit_link(pilot, app)
            assert isinstance(app.screen, MetadataScreen)

            main_row = app.screen.query_one("#main_row", Horizontal)
            column = main_row.query_one(".column", Vertical)
            preview = main_row.query_one("#cover_preview", Vertical)
            assert preview is not None

            fields = column.query(
                "#cover, #title, #author, #description, #genres, #language, "
                "#date, #publisher, #series, #series_index"
            )
            assert len(fields) == 10

            kids = list(column.children)
            assert kids[1] is column.query_one("#cover", Select)

    async def test_preview_readable_at_80_columns(self) -> None:
        load, _ = make_preview({"https://cover/x1.jpg": tiny_image()})
        app = ExporterApp(
            load_metadata=build_loader(covers=COVERS),
            fetch_preview=load,
            supports_terminal_images=terminal_supports_images,
        )

        async with app.run_test(size=(80, 24)) as pilot:
            await submit_link(pilot, app)
            assert isinstance(app.screen, MetadataScreen)
            assert app.screen.query_one("#main_row", Horizontal).styles.layout.name == "vertical"

            app.screen.query_one("#cover", Select).value = "18021734"
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.screen.query_one("#title", Input) is not None
            assert app.screen.query_one("#cover", Select) is not None
            assert app.screen.query_one("#next", Button) is not None
            assert app.screen.query_one("#cover_image", ImageWidget).display is True


class TestConfirmScreen:
    """Задачи 3.1–3.4, 4.2: редактируемая форма параметров выгрузки."""

    async def test_form_is_prefilled_from_options(self) -> None:
        app = ExporterApp(
            load_metadata=loader(single_team_chapters()),
            options=Options(
                rate_limit=7.0,
                retries=5,
                include_images=False,
                max_image_mb=2.0,
                max_image_width=900,
                quality=70,
                output=Path("book.epub"),
                chapters="1-3",
            ),
        )

        async with app.run_test() as pilot:
            await enter_metadata(pilot, app)

            assert isinstance(app.screen, ConfirmScreen)
            assert app.screen.query_one("#rate_limit", Input).value == "7.0"
            assert app.screen.query_one("#retries", Input).value == "5"
            assert app.screen.query_one("#max_image_mb", Input).value == "2.0"
            assert app.screen.query_one("#max_image_width", Input).value == "900"
            assert app.screen.query_one("#quality", Input).value == "70"
            assert app.screen.query_one("#output", Input).value == "book.epub"
            assert app.screen.query_one("#chapters", Input).value == "1-3"
            assert app.screen.query_one("#include_images", Checkbox).value is False
            assert len(app.screen.query(".field_label")) == 8

    async def test_edits_flow_into_build_plan(self) -> None:
        captured: dict = {}

        async def build(plan, on_progress, on_notice):
            captured["plan"] = plan
            recorder = ReportRecorder(total_chapters=len(plan.chapters))
            recorder.finished("/tmp/book.epub", 1)
            return recorder

        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=build)

        async with app.run_test() as pilot:
            await enter_metadata(pilot, app)
            assert isinstance(app.screen, ConfirmScreen)
            app.screen.query_one("#rate_limit", Input).value = "11"
            app.screen.query_one("#chapters", Input).value = "2-4"
            await pilot.click("#start")
            await app.workers.wait_for_complete()
            await pilot.pause()

        plan = captured["plan"]
        assert plan.options.rate_limit == 11.0
        assert [c.id for c in plan.chapters] == [2, 3, 4]

    async def test_invalid_value_stays_and_shows_error(self) -> None:
        app = ExporterApp(load_metadata=loader(single_team_chapters()))

        async with app.run_test() as pilot:
            await enter_metadata(pilot, app)
            app.screen.query_one("#rate_limit", Input).value = "abc"
            await pilot.click("#start")
            await pilot.pause()

            assert isinstance(app.screen, ConfirmScreen)
            assert "Ошибка" in str(app.screen.query_one("#form_error", Static).content)

    async def test_charset_is_not_editable_in_form(self) -> None:
        app = ExporterApp(load_metadata=loader(single_team_chapters()))

        async with app.run_test() as pilot:
            await enter_metadata(pilot, app)

            assert len(app.screen.query("#charset")) == 0

    async def test_preset_selection_fills_and_disables_fields(self) -> None:
        app = ExporterApp(load_metadata=loader(single_team_chapters()))

        async with app.run_test() as pilot:
            await enter_metadata(pilot, app)
            app.screen.query_one("#preset", Select).value = "crosspoint"
            await pilot.pause()

            assert app.screen.query_one("#max_image_width", Input).value == "480"
            assert app.screen.query_one("#quality", Input).value == "80"
            assert app.screen.query_one("#max_image_mb", Input).value == "0.5"
            assert app.screen.query_one("#max_image_width", Input).disabled is True
            assert app.screen.query_one("#quality", Input).disabled is True
            assert app.screen.query_one("#max_image_mb", Input).disabled is True

    async def test_returning_to_manual_unlocks_fields(self) -> None:
        app = ExporterApp(load_metadata=loader(single_team_chapters()))

        async with app.run_test() as pilot:
            await enter_metadata(pilot, app)
            preset = app.screen.query_one("#preset", Select)
            preset.value = "medium"
            await pilot.pause()
            assert app.screen.query_one("#quality", Input).disabled is True

            preset.value = MANUAL_PRESET
            await pilot.pause()

            assert app.screen.query_one("#quality", Input).disabled is False
            assert app.screen.query_one("#max_image_width", Input).disabled is False

    async def test_preset_selection_flows_into_build_plan(self) -> None:
        captured: dict = {}

        async def build(plan, on_progress, on_notice):
            captured["plan"] = plan
            recorder = ReportRecorder(total_chapters=len(plan.chapters))
            recorder.finished("/tmp/book.epub", 1)
            return recorder

        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=build)

        async with app.run_test() as pilot:
            await enter_metadata(pilot, app)
            app.screen.query_one("#preset", Select).value = "max-compression"
            await pilot.pause()
            app.screen.query_one("#quality", Input).value = "99"
            await pilot.click("#start")
            await app.workers.wait_for_complete()
            await pilot.pause()

        options = captured["plan"].options
        assert options.preset == "max-compression"
        assert (options.max_image_width, options.quality, options.max_image_mb) == (640, 60, 0.5)

    async def test_default_load_metadata_keeps_all_chapters(self, monkeypatch) -> None:
        class FakeClient:
            def __init__(self, config) -> None:
                self.config = config

            async def aclose(self) -> None:
                return None

        class FakeSource:
            def __init__(self, client) -> None:
                self.client = client

            async def fetch_book(self, slug: str) -> Book:
                return BOOK

            async def fetch_chapters(self, slug: str) -> list[Chapter]:
                return multi_team_chapters()

            async def fetch_covers(self, slug: str) -> tuple[Cover, ...]:
                return COVERS

        monkeypatch.setattr("ranobelib_epub.source.client.RanobeLibClient", FakeClient)
        monkeypatch.setattr("ranobelib_epub.source.api.RanobeLibSource", FakeSource)

        meta = await default_load_metadata(LINK, Options(chapters="1-3"))

        assert [c.id for c in meta.chapters] == list(range(1, 11)), (
            "выборка глав применяется на этапе сборки, а не загрузки метаданных"
        )


class TestProgressScreen:
    async def test_progress_and_images_update(self) -> None:
        build = GatedBuild(total=10, prompts=3)
        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=build)

        async with app.run_test(size=(80, 24)) as pilot:
            await enter_confirm(pilot, app)
            await pilot.pause(0.15)

            assert isinstance(app.screen, ProgressScreen)
            assert "3/10" in str(app.screen.query_one("#chapter_info", Static).content)
            assert "3/3" in str(app.screen.query_one("#images_info", Static).content)
            assert app.screen.query_one("#chapters", ProgressBar).progress == 3

    async def test_download_phase_is_visible_before_first_chapter(self) -> None:
        gate = asyncio.Event()

        async def build(plan, on_progress, on_notice):
            on_progress(Progress(fetched=3, total=10, elapsed=2.0))
            await asyncio.sleep(0)
            await gate.wait()
            recorder = ReportRecorder(total_chapters=10)
            recorder.finished("/tmp/book.epub", 1)
            return recorder

        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=build)

        async with app.run_test(size=(80, 24)) as pilot:
            await enter_confirm(pilot, app)
            await pilot.pause(0.15)

            assert isinstance(app.screen, ProgressScreen)
            assert "Скачивание" in str(app.screen.query_one("#chapter_info", Static).content)
            assert app.screen.query_one("#chapters", ProgressBar).progress == 3

            gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()

    async def test_slow_build_does_not_block_ui(self) -> None:
        build = GatedBuild(total=10, prompts=3)
        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=build)
        ticks = 0

        async with app.run_test() as pilot:
            await enter_confirm(pilot, app)
            assert isinstance(app.screen, ProgressScreen)

            for _ in range(3):
                await pilot.pause(0.05)
                ticks += 1

            assert ticks == 3, "цикл событий не вращался, пока шла сборка"
            assert "3/10" in str(app.screen.query_one("#chapter_info", Static).content)

            build.gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)

    async def test_journal_panel_is_present_and_bounded(self) -> None:
        build = GatedBuild(total=10, prompts=1)
        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=build)

        async with app.run_test(size=(120, 40)) as pilot:
            await enter_confirm(pilot, app)
            await pilot.pause(0.15)

            assert isinstance(app.screen, ProgressScreen)
            panel = app.screen.query_one("#log_panel", Collapsible)
            log = app.screen.query_one("#chapter_log", RichLog)

            assert panel.collapsed is True, "панель стартует свёрнутой"
            assert log.max_lines is not None and log.max_lines <= 10_000
            assert log.auto_scroll is False, "прокрутка не должна навязываться"

            build.gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()

    async def test_journal_panel_shows_chapter_messages(self) -> None:
        build = JournalBuild(total=5)
        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=build)

        async with app.run_test(size=(120, 40)) as pilot:
            await enter_confirm(pilot, app)
            await pilot.pause(0.15)

            assert isinstance(app.screen, ProgressScreen)
            app.screen.action_toggle_log()
            await pilot.pause(0.1)
            log = app.screen.query_one("#chapter_log", RichLog)
            text = "\n".join(str(line) for line in log.lines)

            assert "1.1" in text and "собрана" in text
            assert "1.2" in text and "пропущена" in text
            assert "таймаут запроса" in text

            build.gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)

    async def test_toggling_journal_does_not_interrupt_build(self) -> None:
        build = GatedBuild(total=10, prompts=3)
        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=build)

        async with app.run_test(size=(120, 40)) as pilot:
            await enter_confirm(pilot, app)
            await pilot.pause(0.15)

            assert isinstance(app.screen, ProgressScreen)
            panel = app.screen.query_one("#log_panel", Collapsible)
            assert panel.collapsed is True

            await pilot.press("l")
            await pilot.pause()
            assert panel.collapsed is False, "биндинг разворачивает панель"

            build.gate.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen), "сборка не прервалась переключением"


class TestReportScreen:
    async def test_success_shows_path_and_size(self) -> None:
        app = ExporterApp(load_metadata=loader(single_team_chapters()), build=fast_build())

        async with app.run_test() as pilot:
            await enter_confirm(pilot, app)
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert isinstance(app.screen, ReportScreen)
            assert "/tmp/book.epub" in app.report_text
            assert "размер файла" in app.report_text

    async def test_interrupt_reports_built_chapters_and_offers_save(self) -> None:
        saved: list[str] = []

        async def save() -> Path:
            saved.append("called")
            return Path("/tmp/partial.epub")

        app = ExporterApp(
            load_metadata=loader(single_team_chapters()),
            build=interrupting_build(),
            save_partial=save,
        )

        async with app.run_test() as pilot:
            await enter_confirm(pilot, app)
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert isinstance(app.screen, ReportScreen)
            assert app.interrupted is True
            assert "собрано глав: 4 из 10" in app.report_text

            await pilot.click("#save")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert "partial.epub" in str(app.screen.query_one("#save_status", Static).content)

    async def test_narrow_terminal_is_readable(self) -> None:
        app = ExporterApp(load_metadata=loader(multi_team_chapters()), build=fast_build())

        async with app.run_test(size=(80, 24)) as pilot:
            await submit_link(pilot, app)
            assert isinstance(app.screen, TranslationScreen)
            assert app.screen.query_one("#prompt", Static) is not None
            await pilot.click("#continue")
            await pilot.pause()
            assert isinstance(app.screen, MetadataScreen)
            await pilot.click("#next")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#start")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, ReportScreen)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_all_screens_mount_at_size(size: tuple[int, int]) -> None:
    app = ExporterApp(load_metadata=loader(single_team_chapters()), build=fast_build())

    async with app.run_test(size=size) as pilot:
        await enter_confirm(pilot, app)
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, ReportScreen)
