"""Тесты консольного интерфейса (задачи 13.1-13.6)."""

from __future__ import annotations

import asyncio
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ranobelib_epub.cli.main import (
    EXIT_BAD_ARGUMENT,
    EXIT_FAILED,
    EXIT_OK,
    Printer,
    configure_output_encoding,
    list_covers_flow,
    main,
    progress_line,
    run,
    run_build,
)
from ranobelib_epub.cli.options import (
    ArgumentError,
    Options,
    apply_chapter_selection,
    build_options,
    charset_profile,
    default_filename,
    exit_code_for,
    jpeg_quality,
    parse_args,
    parse_chapter_selection,
    positive_float,
    positive_int,
)
from ranobelib_epub.models import Book, Chapter, MetadataOverrides, apply_overrides
from ranobelib_epub.pipeline.report import ChapterEvent, Progress

URL = "https://ranobelib.me/book/94231--rezero"
FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FixtureClient:
    """Подделка клиента: отдаёт фикстуры по форме пути, без сети."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self.paths.append(path)
        if path.endswith("/chapters"):
            return load("chapters.json")
        if path.endswith("/chapter"):
            return load("chapter.json")
        if path.endswith("/covers"):
            return load("covers.json")
        return load("book.json")

    async def aclose(self) -> None:
        return None


class TestArguments:
    def test_all_parameters_are_parsed(self, tmp_path: Path) -> None:
        options = parse_args(
            [
                URL,
                "--output",
                str(tmp_path / "out.epub"),
                "--no-images",
                "--max-image-mb",
                "2.5",
                "--max-image-width",
                "1000",
                "--charset",
                "full",
                "--team",
                "Loxotron's Translations",
                "--chapters",
                "1-10",
                "--rate-limit",
                "8",
                "--retries",
                "5",
                "--no-tui",
            ]
        )

        assert options.slug_url == URL
        assert options.output == tmp_path / "out.epub"
        assert options.include_images is False
        assert options.max_image_mb == 2.5
        assert options.max_image_width == 1000
        assert options.charset == "full"
        assert options.team == "Loxotron's Translations"
        assert options.chapters == "1-10"
        assert options.rate_limit == 8
        assert options.retries == 5
        assert options.no_tui is True

    def test_defaults_match_specification(self) -> None:
        options = parse_args([URL])

        assert options.include_images is True
        assert options.max_image_width == 1280
        assert options.quality == 80
        assert options.charset == "builtin"
        assert options.rate_limit == 4.0
        assert options.retries == 3
        assert options.max_image_mb > 0

    def test_no_tui_flag(self) -> None:
        assert parse_args([URL, "--no-tui"]).no_tui is True

    def test_invalid_float_reports_error(self) -> None:
        with pytest.raises(ArgumentError, match="ожидалось число"):
            parse_args([URL, "--max-image-mb", "abc"])

    def test_negative_value_reports_error(self) -> None:
        with pytest.raises(ArgumentError, match="отрицательным"):
            parse_args([URL, "--max-image-width", "-5"])

    def test_invalid_charset_reports_error(self) -> None:
        with pytest.raises(ArgumentError, match="неизвестный профиль"):
            parse_args([URL, "--charset", "wingdings"])

    def test_invalid_chapters_reports_error(self) -> None:
        with pytest.raises(ArgumentError):
            parse_args([URL, "--chapters", "abc"])

    def test_missing_book_argument_is_allowed(self) -> None:
        assert parse_args([]).slug_url is None

    def test_summary_lists_effective_parameters(self) -> None:
        lines = "\n".join(parse_args([URL, "--no-images"]).summary_lines())

        assert "изображения: выключены" in lines
        assert "ширина: 1280" in lines
        assert "набор символов: builtin" in lines


class TestChapterSelection:
    def test_range(self) -> None:
        _, _, indexes, _ = parse_chapter_selection("1-50")

        assert indexes == frozenset(range(1, 51))

    def test_list(self) -> None:
        _, _, indexes, _ = parse_chapter_selection("1,3,5")

        assert indexes == frozenset({1, 3, 5})

    def test_mixed_list(self) -> None:
        _, _, indexes, _ = parse_chapter_selection("1-3,7,10-12")

        assert indexes == frozenset({1, 2, 3, 7, 10, 11, 12})

    def test_volume(self) -> None:
        _, volumes, _, _ = parse_chapter_selection("v2")

        assert volumes == frozenset({2})

    def test_volume_range(self) -> None:
        _, volumes, _, _ = parse_chapter_selection("v2-3")

        assert volumes == frozenset({2, 3})

    def test_wide_volume_range(self) -> None:
        _, volumes, _, _ = parse_chapter_selection("v4-9")

        assert volumes == frozenset({4, 5, 6, 7, 8, 9})

    def test_open_volume_range(self) -> None:
        _, volumes, _, volume_min = parse_chapter_selection("v44-")

        assert volumes == frozenset()
        assert volume_min == 44

    def test_mixed_volume_and_chapter_selection(self) -> None:
        _, volumes, indexes, _ = parse_chapter_selection("1-3,v2-2")

        assert volumes == frozenset({2})
        assert indexes == frozenset({1, 2, 3})

    def test_invalid_volume_range_rejected(self) -> None:
        with pytest.raises(ArgumentError, match="диапазон"):
            parse_chapter_selection("v3-2")
        with pytest.raises(ArgumentError, match="диапазон томов"):
            parse_chapter_selection("vx-2")

    def test_invalid_open_volume_range_rejected(self) -> None:
        with pytest.raises(ArgumentError, match="диапазон томов"):
            parse_chapter_selection("vx-")

    def test_selection_applies_volume_range(self) -> None:
        options = parse_args([URL, "--chapters", "v2-3"])
        chapters = [
            Chapter(id=index, volume=volume, number=str(index), name="n", label=f"{volume}.{index}")
            for index, volume in enumerate([1, 2, 2, 3, 4], start=1)
        ]

        selected = options.selection.apply(chapters)

        assert [c.volume for c in selected] == [2, 2, 3]

    def test_selection_applies_open_volume_range(self) -> None:
        options = parse_args([URL, "--chapters", "v2-"])
        chapters = [
            Chapter(id=index, volume=volume, number=str(index), name="n", label=f"{volume}.{index}")
            for index, volume in enumerate([1, 2, 3, 4], start=1)
        ]

        selected = options.selection.apply(chapters)

        assert [c.volume for c in selected] == [2, 3, 4]

    def test_backwards_range_rejected(self) -> None:
        with pytest.raises(ArgumentError, match="конец диапазона"):
            parse_chapter_selection("10-1")

    def test_empty_selection_rejected(self) -> None:
        with pytest.raises(ArgumentError, match="пустой"):
            parse_chapter_selection(" , ")

    def test_selection_applies_to_chapters(self) -> None:
        from ranobelib_epub.models import Chapter

        options = parse_args([URL, "--chapters", "2-3"])
        chapters = [
            Chapter(id=index, volume=1, number=str(index), name="n", label=f"1.{index}")
            for index in range(1, 6)
        ]

        selected = options.selection.apply(chapters)

        assert [c.id for c in selected] == [2, 3]


class TestEditableOptions:
    """Задача 4.1: переиспользуемые проверки и сборка Options из формы."""

    def test_validators_accept_well_formed_values(self) -> None:
        assert positive_float("2.5") == 2.5
        assert positive_int("8") == 8
        assert jpeg_quality("80") == 80
        assert charset_profile("full") == "full"

    @pytest.mark.parametrize(
        "call",
        [
            lambda: positive_float("abc"),
            lambda: positive_float("-1"),
            lambda: positive_int("-1"),
            lambda: positive_int("1.5"),
            lambda: jpeg_quality("0"),
            lambda: jpeg_quality("101"),
            lambda: charset_profile("wingdings"),
        ],
    )
    def test_validators_reject_bad_values(self, call) -> None:
        with pytest.raises(ArgumentError):
            call()

    def test_build_options_replaces_editable_fields(self, tmp_path: Path) -> None:
        base = parse_args([URL, "--charset", "full", "--team", "V"])

        options = build_options(
            base,
            rate_limit="9",
            retries="7",
            include_images=False,
            max_image_mb="3.5",
            max_image_width="1200",
            quality="75",
            output=str(tmp_path / "x.epub"),
            chapters="2-4",
        )

        assert options.rate_limit == 9.0
        assert options.retries == 7
        assert options.include_images is False
        assert options.max_image_mb == 3.5
        assert options.max_image_width == 1200
        assert options.quality == 75
        assert options.output == tmp_path / "x.epub"
        assert options.chapters == "2-4"
        assert options.charset == "full", "не редактируемый в TUI профиль сохраняется"
        assert options.team == "V"
        assert options.slug_url == base.slug_url

    def test_build_options_blank_output_and_chapters_mean_defaults(self) -> None:
        options = build_options(
            Options(),
            rate_limit="4",
            retries="3",
            include_images=True,
            max_image_mb="0",
            max_image_width="800",
            quality="90",
            output="  ",
            chapters=" ",
        )

        assert options.output is None
        assert options.chapters is None

    def test_build_options_propagates_argument_error(self) -> None:
        with pytest.raises(ArgumentError, match="качество"):
            build_options(
                Options(),
                rate_limit="4",
                retries="3",
                include_images=True,
                max_image_mb="0",
                max_image_width="800",
                quality="0",
                output="",
                chapters="",
            )

    def test_apply_chapter_selection_filters_chapters(self) -> None:
        options = parse_args([URL, "--chapters", "2-3"])
        chapters = [
            Chapter(id=index, volume=1, number=str(index), name="n", label=f"1.{index}")
            for index in range(1, 6)
        ]

        assert [c.id for c in apply_chapter_selection(options, chapters)] == [2, 3]

    def test_apply_chapter_selection_without_selection_returns_all(self) -> None:
        chapters = [
            Chapter(id=index, volume=1, number=str(index), name="n", label=f"1.{index}")
            for index in range(1, 4)
        ]

        assert apply_chapter_selection(Options(), chapters) == chapters

    def test_chapters_editing_affects_build_plan(self) -> None:
        """Задача 4.2 (CLI-часть): правка выборки меняет набор собранных глав."""
        options = parse_args([URL, "--no-tui", "--chapters", "1-20"])
        chapters = _five_chapters()

        assert [c.id for c in apply_chapter_selection(options, chapters)] == [1, 2, 3, 4, 5]

        narrowed = build_options(
            options,
            rate_limit="4",
            retries="3",
            include_images=False,
            max_image_mb="0",
            max_image_width="800",
            quality="80",
            output="",
            chapters="2-3",
        )

        assert [c.id for c in apply_chapter_selection(narrowed, chapters)] == [2, 3]


def _five_chapters() -> list[Chapter]:
    return [
        Chapter(id=index, volume=1, number=str(index), name="n", label=f"1.{index}")
        for index in range(1, 6)
    ]


class TestOutputName:
    def test_russian_name_used(self) -> None:
        book = Book(slug_url=URL, rus_name="Re:Zero — Возвращение", name="Re:Zero")

        assert default_filename(book) == "Re -Zero — Возвращение.epub"

    def test_original_name_when_no_russian(self) -> None:
        book = Book(slug_url=URL, name="Re:Zero kara Hajimeru Isekai Seikatsu")

        assert default_filename(book) == "Re -Zero kara Hajimeru Isekai Seikatsu.epub"

    def test_forbidden_characters_replaced(self) -> None:
        book = Book(slug_url=URL, rus_name='Книга/1: "Часть"*2', name="x")

        name = default_filename(book)

        assert name.endswith(".epub")
        assert not set('/:*"') & set(name)

    def test_output_argument_wins(self, tmp_path: Path) -> None:
        book = Book(slug_url=URL, rus_name="Книга")
        options = Options(output=tmp_path / "my.epub")

        assert options.output_path_for(book) == tmp_path / "my.epub"

    def test_slug_used_when_no_title(self) -> None:
        assert default_filename(Book(slug_url="94231--x")).endswith(".epub")

    def test_empty_title_does_not_produce_empty_name(self) -> None:
        assert default_filename(Book(slug_url="", name="")).endswith(".epub")


class TestExitCodes:
    def test_success_returns_zero(self) -> None:
        assert main(["--help"]) == EXIT_OK
        assert exit_code_for(None) == 0

    def test_bad_argument_returns_two(self) -> None:
        assert main(["https://x/y", "--max-image-mb", "abc"]) == EXIT_BAD_ARGUMENT
        assert exit_code_for(ArgumentError("x")) == 2

    def test_missing_book_returns_nonzero(self, capsys) -> None:
        code = main([])

        assert code == EXIT_FAILED
        assert "ссылка" in capsys.readouterr().err

    def test_build_failure_returns_one(self, monkeypatch, capsys) -> None:
        from ranobelib_epub.source.client import ApiUnavailableError

        def boom(*args, **kwargs):
            raise ApiUnavailableError("сеть недоступна")

        main_module = sys.modules["ranobelib_epub.cli.main"]

        monkeypatch.setattr(main_module, "RanobeLibClient", boom)

        code = main([URL, "--no-tui"])

        assert code == EXIT_FAILED
        assert "сеть недоступна" in capsys.readouterr().err

    def test_unknown_book_returns_one(self, monkeypatch, capsys) -> None:
        from ranobelib_epub.source.client import BookNotFoundError

        class FailingClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def get_json(self, *args, **kwargs):
                raise BookNotFoundError("книга не найдена")

            async def aclose(self) -> None:
                pass

        main_module = sys.modules["ranobelib_epub.cli.main"]

        monkeypatch.setattr(main_module, "RanobeLibClient", FailingClient)

        code = main([URL, "--no-tui"])

        assert code == EXIT_FAILED
        assert "книга не найдена" in capsys.readouterr().err

    def test_error_goes_to_stderr(self, capsys) -> None:
        main(["https://x/y", "--max-image-mb", "abc"])

        captured = capsys.readouterr()
        assert "Ошибка" in captured.err
        assert captured.out == ""


class TestNonInteractiveOutput:
    def test_progress_is_plain_lines(self) -> None:
        stream = io.StringIO()
        printer = Printer(stream=stream)

        printer.line("первая")
        printer.line("вторая")

        assert stream.getvalue().splitlines() == ["первая", "вторая"]

    def test_quiet_prints_nothing(self) -> None:
        stream = io.StringIO()
        printer = Printer(stream=stream, quiet=True)

        printer.line("тишина")

        assert stream.getvalue() == ""

    def test_progress_render_is_single_line(self) -> None:
        from ranobelib_epub.pipeline.report import Progress

        text = Progress(done=5, total=10, elapsed=2.0).render()

        assert "\n" not in text
        assert "5/10" in text


class TestJournalOutput:
    def test_event_line_preferred_over_aggregate(self) -> None:
        event = ChapterEvent(position=3, total=20, label="1.3", name="Глава", built=True, images=1)
        progress = Progress(done=3, total=20, elapsed=1.0, last_event=event)

        assert progress_line(progress) == event.render()

    def test_aggregate_used_without_event(self) -> None:
        progress = Progress(done=3, total=20, elapsed=1.0)

        assert progress_line(progress) == progress.render()

    def test_unavailable_line_names_number_name_and_reason(self) -> None:
        event = ChapterEvent(
            position=2,
            total=20,
            label="1.2",
            name="Глава",
            built=False,
            reason="таймаут запроса",
        )

        text = progress_line(Progress(done=1, total=20, last_event=event))

        assert "1.2" in text
        assert "Глава" in text
        assert "таймаут запроса" in text

    def test_quiet_printer_suppresses_journal_lines(self) -> None:
        stream = io.StringIO()
        printer = Printer(stream=stream, quiet=True)
        event = ChapterEvent(position=1, total=1, label="1.1", name="Глава", built=True)

        printer.line(progress_line(Progress(done=1, total=1, last_event=event)))

        assert stream.getvalue() == ""

    def test_no_tui_prints_one_line_per_chapter(self, tmp_path: Path) -> None:
        options = parse_args(
            [
                URL,
                "--no-tui",
                "--no-images",
                "--chapters",
                "1-20",
                "--output",
                str(tmp_path / "book.epub"),
            ]
        )
        stream = io.StringIO()
        printer = Printer(stream=stream)

        asyncio.run(run_build(options, printer, client=FixtureClient()))

        journal = [line for line in stream.getvalue().splitlines() if line.startswith("[")]
        assert len(journal) == 20, "по строке на каждую из 20 глав"
        positions = [int(line.split("]")[0].lstrip("[").split("/")[0]) for line in journal]
        assert positions == list(range(1, 21))
        assert all("собрана" in line for line in journal)
        assert (tmp_path / "book.epub").exists()


class TestConsoleScript:
    def test_help_exits_zero(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "ranobelib_epub.cli.main", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 0
        assert "ranobelib-epub" in completed.stdout

    def test_bad_value_exits_nonzero_without_building(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "ranobelib_epub.cli.main",
                "https://x/y",
                "--max-image-mb",
                "abc",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == EXIT_BAD_ARGUMENT
        assert "Ошибка" in completed.stderr

    def test_installed_script_prints_help(self) -> None:
        completed = subprocess.run(
            ["uv", "run", "ranobelib-epub", "--help"], capture_output=True, text=True, check=False
        )

        assert completed.returncode == 0
        assert "--charset" in completed.stdout


class TestCompressionPresets:
    def test_defaults_equal_medium_preset(self) -> None:
        options = parse_args([URL])

        assert options.preset is None
        assert (options.max_image_width, options.quality, options.max_image_mb) == (1280, 80, 0.5)
        assert options.grayscale is False

    def test_preset_sets_effective_values(self) -> None:
        options = parse_args([URL, "--preset", "crosspoint"])

        assert options.preset == "crosspoint"
        assert options.max_image_width == 480
        assert options.quality == 80
        assert options.grayscale is True

    def test_manual_width_stays_manual(self) -> None:
        options = parse_args([URL, "--max-image-width", "800"])

        assert options.preset is None
        assert options.max_image_width == 800

    def test_manual_quality_is_parsed(self) -> None:
        options = parse_args([URL, "--quality", "60"])

        assert options.preset is None
        assert options.quality == 60

    def test_preset_conflicts_with_manual_parameter(self) -> None:
        with pytest.raises(ArgumentError, match="нельзя сочетать"):
            parse_args([URL, "--preset", "medium", "--quality", "60"])

    def test_unknown_preset_is_rejected(self) -> None:
        with pytest.raises(ArgumentError, match="неизвестный пресет"):
            parse_args([URL, "--preset", "turbo"])

    def test_summary_shows_preset_name(self) -> None:
        lines = "\n".join(parse_args([URL, "--preset", "max-compression"]).summary_lines())

        assert "пресет: max-compression" in lines

    def test_summary_shows_manual_mode(self) -> None:
        lines = "\n".join(parse_args([URL]).summary_lines())

        assert "пресет: вручную" in lines

    def test_crosspoint_preset_reaches_downloader(self, monkeypatch, tmp_path: Path) -> None:
        captured: dict = {}

        class StubDownloader:
            def __init__(self, *args, **kwargs) -> None:
                captured.update(kwargs)

            async def fetch_all(self, tasks, book_slug):
                return []

            async def fetch_cover(self, book):
                return None

        main_module = sys.modules["ranobelib_epub.cli.main"]

        monkeypatch.setattr(main_module, "ChapterDownloader", StubDownloader)

        options = parse_args(
            [
                URL,
                "--no-tui",
                "--preset",
                "crosspoint",
                "--chapters",
                "1",
                "--output",
                str(tmp_path / "b.epub"),
            ]
        )

        asyncio.run(run_build(options, Printer(stream=io.StringIO()), client=FixtureClient()))

        assert captured["grayscale"] is True
        assert captured["max_image_width"] == 480

    def test_crosspoint_preset_produces_grayscale_epub(self, monkeypatch, tmp_path: Path) -> None:
        import zipfile

        from PIL import Image as PILImage

        from ranobelib_epub.models import Attachment, ChapterContent

        png = _png_bytes()

        class StubSource:
            def __init__(self, client) -> None:
                self.client = client

            async def fetch_book(self, slug):
                return Book(slug_url=URL, rus_name="Книга", name="Книга")

            async def fetch_chapters(self, slug):
                return [
                    Chapter(id=1, volume=1, number="1", name="Глава", label="1.1", branch_id=7)
                ]

            async def fetch_chapter_content(self, slug, chapter, branch_id):
                return ChapterContent(
                    doc={
                        "type": "doc",
                        "content": [
                            {"type": "image", "attrs": {"images": [{"image": "a"}]}},
                            {"type": "paragraph", "content": [{"type": "text", "text": "Текст"}]},
                        ],
                    },
                    attachments=(Attachment(name="a", extension="png", url="/uploads/a.png"),),
                    branch_id=7,
                )

        class ByteClient(FixtureClient):
            async def get_bytes(self, url: str, headers: dict | None = None) -> bytes:
                return png

        main_module = sys.modules["ranobelib_epub.cli.main"]

        monkeypatch.setattr(main_module, "RanobeLibSource", StubSource)
        monkeypatch.setattr(main_module, "apply_selection", lambda chapters, team: chapters)

        target = tmp_path / "book.epub"
        options = parse_args([URL, "--no-tui", "--preset", "crosspoint", "--output", str(target)])
        asyncio.run(run_build(options, Printer(stream=io.StringIO()), client=ByteClient()))

        with zipfile.ZipFile(target) as archive:
            image_name = next(n for n in archive.namelist() if n.startswith("EPUB/Images/"))
            data = archive.read(image_name)

        with PILImage.open(io.BytesIO(data)) as image:
            assert image.mode == "L"


def _png_bytes() -> bytes:
    from PIL import Image as PILImage

    buffer = io.BytesIO()
    PILImage.new("RGB", (60, 40), (200, 10, 10)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestMetadataFlags:
    def test_metadata_flags_are_parsed(self) -> None:
        options = parse_args(
            [
                URL,
                "--title",
                "Заглавие",
                "--author",
                "Автор",
                "--description",
                "Описание",
                "--language",
                "en",
                "--subjects",
                "Драма, Фэнтези",
                "--date",
                "2024-05-01",
                "--publisher",
                "Издатель",
                "--series",
                "Серия",
                "--series-index",
                "3",
            ]
        )

        assert options.title == "Заглавие"
        assert options.author == "Автор"
        assert options.description == "Описание"
        assert options.language == "en"
        assert options.subjects == "Драма, Фэнтези"
        assert options.date == "2024-05-01"
        assert options.publisher == "Издатель"
        assert options.series == "Серия"
        assert options.series_index == 3

    def test_cover_flags_are_parsed(self) -> None:
        options = parse_args([URL, "--cover", "18021734", "--no-cover", "--list-covers"])

        assert options.cover_id == 18021734
        assert options.cover_disabled is True
        assert options.list_covers is True

    def test_defaults_keep_metadata_unchanged(self) -> None:
        options = parse_args([URL])

        assert options.title is None
        assert options.language is None
        assert options.cover_id is None
        assert options.cover_disabled is False
        assert options.list_covers is False

    @pytest.mark.parametrize(
        "argv_fragment",
        [
            ["--language", "xx"],
            ["--date", "2020-13-40"],
            ["--date", "не дата"],
            ["--series-index", "abc"],
            ["--series-index", "-1"],
            ["--cover", "не число"],
        ],
    )
    def test_invalid_metadata_values_are_rejected(self, argv_fragment: list[str]) -> None:
        with pytest.raises(ArgumentError):
            parse_args([URL, *argv_fragment])


class TestMetadataOverridesProperty:
    def test_builds_overrides_from_flags(self) -> None:
        options = parse_args(
            [URL, "--title", "T", "--language", "ja", "--subjects", " a , b "]
        )

        overrides = options.metadata_overrides

        assert overrides.title == "T"
        assert overrides.language == "ja"
        assert overrides.genres == ("a", "b")

    def test_empty_subjects_stay_none(self) -> None:
        options = parse_args([URL])

        assert options.metadata_overrides.genres is None

    def test_empty_subjects_string_means_no_override(self) -> None:
        options = Options(subjects=" , , ")

        assert options.metadata_overrides.genres is None


class TestListCovers:
    def test_prints_covers_with_ids_without_building(self) -> None:
        stream = io.StringIO()
        options = parse_args([URL])

        asyncio.run(list_covers_flow(options, Printer(stream=stream), client=FixtureClient()))

        text = stream.getvalue()
        assert "Re:Zero" in text
        assert "18021734" in text
        assert "Том 1" in text

    def test_empty_covers_hint_uses_default(self) -> None:
        class NoCoversClient(FixtureClient):
            async def get_json(self, path, params=None):
                if path.endswith("/covers"):
                    return {"data": []}
                return await super().get_json(path, params)

        stream = io.StringIO()
        options = parse_args([URL])

        asyncio.run(
            list_covers_flow(options, Printer(stream=stream), client=NoCoversClient())
        )

        assert "Доступных обложек нет" in stream.getvalue()

    def test_missing_book_argument_is_rejected(self) -> None:
        with pytest.raises(ArgumentError, match="ссылка"):
            asyncio.run(list_covers_flow(Options(), Printer(stream=io.StringIO())))


class TestRunBuildWithMetadata:
    def test_overrides_and_cover_reach_build(self, monkeypatch, tmp_path: Path) -> None:
        from datetime import date

        captured: dict = {}

        class StubDownloader:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def fetch_all(self, tasks, book_slug):
                return []

            async def fetch_cover(self, cover_url):
                captured["cover_url"] = cover_url
                return None

        main_module = sys.modules["ranobelib_epub.cli.main"]
        monkeypatch.setattr(main_module, "ChapterDownloader", StubDownloader)

        def fake_write(target, book, fetched, options, recorder, cover=None, build_date=None):
            captured["book"] = book
            captured["build_date"] = build_date
            return 0

        monkeypatch.setattr(main_module, "write_epub", fake_write)

        options = parse_args(
            [
                URL,
                "--no-tui",
                "--title",
                "Новое заглавие",
                "--author",
                "Новый автор",
                "--language",
                "en",
                "--subjects",
                "Приключения, Драма",
                "--series",
                "Re:Zero",
                "--series-index",
                "2",
                "--date",
                "2024-01-01",
                "--cover",
                "18021735",
                "--chapters",
                "1",
                "--output",
                str(tmp_path / "b.epub"),
            ]
        )
        asyncio.run(run_build(options, Printer(stream=io.StringIO()), client=FixtureClient()))

        book = captured["book"]
        assert book.title == "Новое заглавие"
        assert book.author == "Новый автор"
        assert book.in_language == "en"
        assert book.genres == ("Приключения", "Драма")
        assert book.series == "Re:Zero"
        assert book.series_index == 2
        assert captured["build_date"] == date(2024, 1, 1)
        assert captured["cover_url"] == "https://cover.cdnlibs.org/.../cover_1.jpg"

    def test_no_cover_flag_disables_cover(self, monkeypatch, tmp_path: Path) -> None:
        captured: dict = {}

        class StubDownloader:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def fetch_all(self, tasks, book_slug):
                return []

            async def fetch_cover(self, cover_url):
                captured["cover_url"] = cover_url
                return None

        main_module = sys.modules["ranobelib_epub.cli.main"]
        monkeypatch.setattr(main_module, "ChapterDownloader", StubDownloader)
        monkeypatch.setattr(main_module, "write_epub", lambda *a, **k: 0)

        options = parse_args(
            [
                URL,
                "--no-tui",
                "--no-cover",
                "--chapters",
                "1",
                "--output",
                str(tmp_path / "b.epub"),
            ]
        )
        asyncio.run(run_build(options, Printer(stream=io.StringIO()), client=FixtureClient()))

        assert captured["cover_url"] is None

    def test_unknown_cover_id_is_reported_and_dropped(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        captured: dict = {}

        class StubDownloader:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def fetch_all(self, tasks, book_slug):
                return []

            async def fetch_cover(self, cover_url):
                captured["cover_url"] = cover_url
                return None

        main_module = sys.modules["ranobelib_epub.cli.main"]
        monkeypatch.setattr(main_module, "ChapterDownloader", StubDownloader)

        def fake_write(target, book, fetched, options, recorder, cover=None, build_date=None):
            captured["notes"] = list(recorder.report.notes)
            return 0

        monkeypatch.setattr(main_module, "write_epub", fake_write)

        options = parse_args(
            [
                URL,
                "--no-tui",
                "--cover",
                "999999",
                "--chapters",
                "1",
                "--output",
                str(tmp_path / "b.epub"),
            ]
        )
        asyncio.run(run_build(options, Printer(stream=io.StringIO()), client=FixtureClient()))

        assert captured["cover_url"] is None
        assert any("999999" in note and "без обложки" in note for note in captured["notes"])
        assert captured["notes"], "причина недоступности фиксируется в отчёте"

    def test_override_affects_default_filename(self) -> None:
        book = Book(slug_url="94231--rezero", rus_name="Старое", name="Старое")
        overridden = apply_overrides(book, MetadataOverrides(title="Новое заглавие"))

        assert default_filename(overridden) == "Новое заглавие.epub"


def test_help_survives_legacy_stdout_encoding(monkeypatch) -> None:
    """Русская справка не падает при кодировке stdout вроде cp1252 (Windows).

    На Windows с перенаправленным выводом Python берёт кодировку локали
    (cp1252), и нелатинские символы в `--help` роняли бы программу
    UnicodeEncodeError. `configure_output_encoding` переключает потоки на UTF-8.
    """
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    monkeypatch.setattr(sys, "argv", ["ranobelib-epub", "--help"])

    with pytest.raises(SystemExit) as exc:
        run()
    assert exc.value.code == 0

    stream.flush()
    text = buf.getvalue().decode("utf-8")
    assert "Скачивает книгу" in text


def test_configure_output_encoding_skips_streams_without_reconfigure(monkeypatch) -> None:
    """Потоки без reconfigure (например, StringIO) не ломают запуск."""
    plain = io.StringIO()
    monkeypatch.setattr(sys, "stdout", plain)
    monkeypatch.setattr(sys, "stderr", plain)

    configure_output_encoding()
    assert isinstance(sys.stdout, io.StringIO)
