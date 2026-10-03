"""Тесты консольного интерфейса (задачи 13.1-13.6)."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from ranobelib_epub.cli.main import EXIT_BAD_ARGUMENT, EXIT_FAILED, EXIT_OK, Printer, main
from ranobelib_epub.cli.options import (
    ArgumentError,
    Options,
    default_filename,
    exit_code_for,
    parse_args,
    parse_chapter_selection,
)
from ranobelib_epub.models import Book

URL = "https://ranobelib.me/book/94231--rezero"


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
        _, _, indexes = parse_chapter_selection("1-50")

        assert indexes == frozenset(range(1, 51))

    def test_list(self) -> None:
        _, _, indexes = parse_chapter_selection("1,3,5")

        assert indexes == frozenset({1, 3, 5})

    def test_mixed_list(self) -> None:
        _, _, indexes = parse_chapter_selection("1-3,7,10-12")

        assert indexes == frozenset({1, 2, 3, 7, 10, 11, 12})

    def test_volume(self) -> None:
        _, volumes, _ = parse_chapter_selection("v2")

        assert volumes == frozenset({2})

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