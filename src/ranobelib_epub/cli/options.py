"""Разбор аргументов и параметры запуска (задачи 13.1, 13.2, 13.5).

Значения по умолчанию зафиксированы требованием «Параметры по умолчанию соответствуют
заявленным значениям»: изображения включены, сжатие включено, ширина 1280, качество 80,
троттлинг и повторы активны, профиль символов — `builtin`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from ..charset.profiles import DEFAULT_PROFILE, PROFILES
from ..epub.naming import sanitize as sanitize_name
from ..images.pipeline import DEFAULT_MAX_MB, DEFAULT_MAX_WIDTH, DEFAULT_QUALITY
from ..images.presets import DEFAULT_PRESET, PresetError, preset_names, resolve_settings
from ..models import LANGUAGE_CODES, Book, Chapter, MetadataOverrides
from ..source.client import DEFAULT_RATE_LIMIT
from ..source.translations import DEFAULT_TRANSLATION

PROGRAM = "ranobelib-epub"
DESCRIPTION = "Скачивает книгу с RanobeLib и собирает из неё EPUB 3"

DEFAULT_RETRIES = 3
DEFAULT_OUTPUT_SUFFIX = ".epub"


class ArgumentError(ValueError):
    """Некорректное значение параметра: сборка не начинается (13.1)."""


class _Parser(argparse.ArgumentParser):
    """Разборщик, который сообщает об ошибках нашим типом, а не `SystemExit`.

    Иначе неверное значение даёт служебный текст argparse на английском и
    непроносимое отличие между программным вызовом и запуском из терминала.
    """

    def error(self, message: str) -> None:
        raise ArgumentError(message)


def positive_float(value: str) -> float:
    """Неотрицательное число с человеческим сообщением (13.1, задача 1.1)."""
    try:
        number = float(value)
    except ValueError as error:
        raise ArgumentError(f"ожидалось число, получено «{value}»") from error
    if number < 0:
        raise ArgumentError(f"значение не может быть отрицательным: {value}")
    return number


def positive_int(value: str) -> int:
    """Неотрицательное целое с человеческим сообщением (13.1, задача 1.1)."""
    try:
        number = int(value)
    except ValueError as error:
        raise ArgumentError(f"ожидалось целое число, получено «{value}»") from error
    if number < 0:
        raise ArgumentError(f"значение не может быть отрицательным: {value}")
    return number


def jpeg_quality(value: str) -> int:
    """Качество JPEG от 1 до 100 (задача 1.1)."""
    number = positive_int(value)
    if not 1 <= number <= 100:
        raise ArgumentError(f"качество должно быть от 1 до 100, получено «{value}»")
    return number


def charset_profile(value: str) -> str:
    """Известный профиль набора символов (13.1, задача 1.1)."""
    if value not in PROFILES:
        options = ", ".join(PROFILES)
        raise ArgumentError(f"неизвестный профиль «{value}». Доступны: {options}")
    return value


def iso_date(value: str) -> str:
    """Дата строго в формате `YYYY-MM-DD` (задача 2.2)."""
    normalized = value.strip()
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as error:
        raise ArgumentError(
            f"дата должна быть в формате YYYY-MM-DD, получено «{value}»"
        ) from error
    return normalized


def language_code(value: str) -> str:
    """Код языка из фиксированного набора 10 языков (решение 6 design.md)."""
    if value not in LANGUAGE_CODES:
        options = ", ".join(LANGUAGE_CODES)
        raise ArgumentError(f"неизвестный код языка «{value}». Доступны: {options}")
    return value


def _validate(namespace: argparse.Namespace) -> None:
    """Проверки значений с человеческими сообщениями.

    Параметры сжатия могут быть `None` (не заданы) — их разрешает
    `resolve_settings`, а здесь проверяются только явно переданные значения.
    """
    if namespace.max_image_mb is not None:
        positive_float(str(namespace.max_image_mb))
    if namespace.max_image_width is not None:
        positive_int(str(namespace.max_image_width))
    if namespace.quality is not None:
        jpeg_quality(str(namespace.quality))
    if namespace.language is not None:
        language_code(namespace.language)
    if namespace.date is not None:
        iso_date(namespace.date)
    if namespace.series_index is not None:
        positive_int(str(namespace.series_index))
    if namespace.cover is not None:
        positive_int(str(namespace.cover))
    positive_float(str(namespace.rate_limit))
    positive_int(str(namespace.retries))
    charset_profile(namespace.charset)


def parse_chapter_selection(value: str) -> tuple[frozenset[str], frozenset[int], frozenset[int]]:
    """Разбор `--chapters`: диапазоны `1-50`, списки `1,3,5`, номера томов `v2`.

    Поддерживаются смешанные списки: `1-3,7,10-12`. Возвращается тройка
    (метки, тома, порядковые номера) — её же понимает `ChapterSelector`.
    """
    labels: set[str] = set()
    volumes: set[int] = set()
    indexes: set[int] = set()

    for chunk in (part.strip() for part in value.split(",")):
        if not chunk:
            continue
        if chunk.startswith("v") and chunk[1:].isdigit():
            volumes.add(int(chunk[1:]))
            continue
        if "-" in chunk:
            start_text, _, end_text = chunk.partition("-")
            if not start_text.isdigit() or not end_text.isdigit():
                raise ArgumentError(f"некорректный диапазон глав: «{chunk}»")
            start, end = int(start_text), int(end_text)
            if end < start:
                raise ArgumentError(f"конец диапазона меньше начала: «{chunk}»")
            indexes.update(range(start, end + 1))
            continue
        if not chunk.isdigit():
            raise ArgumentError(f"некорректный номер главы: «{chunk}»")
        indexes.add(int(chunk))

    if not (labels or volumes or indexes):
        raise ArgumentError("пустой список глав")
    return frozenset(labels), frozenset(volumes), frozenset(indexes)


@dataclass(frozen=True, slots=True)
class Options:
    """Разобранные параметры запуска."""

    slug_url: str | None = None
    output: Path | None = None
    include_images: bool = True
    max_image_mb: float = DEFAULT_MAX_MB
    max_image_width: int = DEFAULT_MAX_WIDTH
    quality: int = DEFAULT_QUALITY
    grayscale: bool = False
    preset: str | None = None
    charset: str = DEFAULT_PROFILE
    team: str | None = DEFAULT_TRANSLATION
    chapters: str | None = None
    rate_limit: float = DEFAULT_RATE_LIMIT
    retries: int = DEFAULT_RETRIES
    no_tui: bool = False
    title: str | None = None
    author: str | None = None
    description: str | None = None
    language: str | None = None
    subjects: str | None = None
    date: str | None = None
    publisher: str | None = None
    series: str | None = None
    series_index: int | None = None
    cover_id: int | None = None
    cover_disabled: bool = False
    list_covers: bool = False

    @property
    def metadata_overrides(self) -> MetadataOverrides:
        """Переопределения метаданных из флагов; пустые поля означают «с сайта»."""
        genres: tuple[str, ...] | None = None
        if self.subjects is not None:
            parsed = tuple(part.strip() for part in self.subjects.split(",") if part.strip())
            genres = parsed or None
        return MetadataOverrides(
            title=self.title,
            author=self.author,
            description=self.description,
            language=self.language,
            genres=genres,
            date=self.date,
            publisher=self.publisher,
            series=self.series,
            series_index=self.series_index,
        )

    @property
    def selection(self):
        """Выборка глав из `--chapters`; None, если параметр не задан."""
        if self.chapters is None:
            return None
        labels, volumes, indexes = parse_chapter_selection(self.chapters)
        from ..pipeline.downloader import ChapterSelector

        return ChapterSelector(labels=labels, volumes=volumes, indexes=indexes)

    def output_path_for(self, book: Book) -> Path:
        """Итоговый путь: `--output` или имя из названия книги (13.5)."""
        if self.output is not None:
            return self.output
        return Path(default_filename(book))

    def summary_lines(self) -> list[str]:
        """Параметры для показа перед сборкой."""
        images = "включены" if self.include_images else "выключены"
        compression = "выключен" if self.max_image_mb == 0 else f"{self.max_image_mb} МБ"
        return [
            f"изображения: {images}",
            f"пресет: {self.preset or 'вручную'}",
            f"сжатие: {compression}, ширина: {self.max_image_width}, качество: {self.quality}",
            f"набор символов: {self.charset}",
            f"перевод: {self.team or 'актуальная ветка'}",
            f"главы: {self.chapters or 'все'}",
            f"троттлинг: {self.rate_limit}/с, повторы: {self.retries}",
        ]


def build_options(
    base: Options,
    *,
    rate_limit: str,
    retries: str,
    include_images: bool,
    max_image_mb: str,
    max_image_width: str,
    quality: str,
    output: str,
    chapters: str,
    preset: str | None = None,
) -> Options:
    """Собирает `Options` из значений формы TUI, проверяя их общими правилами (задача 1.2).

    Неотредактированные поля (`slug_url`, `charset`, `team`, `no_tui`) переносятся
    из `base` без изменений. Пустой путь вывода означает имя по умолчанию, пустая
    выборка — все главы. Если выбран `preset`, ручные поля сжатия игнорируются и
    берутся значения пресета; иначе разбираются ручные значения (задача 4.2).
    """
    output_path = Path(output.strip()) if output.strip() else None
    chapters_value = chapters.strip() or None
    try:
        resolved = _resolve_for_form(preset, max_image_mb, max_image_width, quality)
    except PresetError as error:
        raise ArgumentError(str(error)) from error
    options = replace(
        base,
        rate_limit=positive_float(rate_limit),
        retries=positive_int(retries),
        include_images=include_images,
        max_image_mb=resolved.max_mb,
        max_image_width=resolved.max_width,
        quality=resolved.quality,
        grayscale=resolved.grayscale,
        preset=resolved.preset,
        output=output_path,
        chapters=chapters_value,
    )
    if options.chapters is not None:
        parse_chapter_selection(options.chapters)
    return options


def _resolve_for_form(
    preset: str | None,
    max_image_mb: str,
    max_image_width: str,
    quality: str,
):
    """Разрешает настройки сжатия из формы TUI: пресет важнее ручных полей."""
    if preset is not None:
        return resolve_settings(preset)
    return resolve_settings(
        None,
        max_width=positive_int(max_image_width),
        quality=jpeg_quality(quality),
        max_mb=positive_float(max_image_mb),
    )


def apply_chapter_selection(options: Options, chapters: list[Chapter]) -> list[Chapter]:
    """Применяет `--chapters` к списку глав перед сборкой (задача 2.2)."""
    selection = options.selection
    if selection is None:
        return chapters
    return selection.apply(chapters)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROGRAM, description=DESCRIPTION)
    parser.add_argument("slug_url", nargs="?", help="ссылка на книгу или её slug_url")
    parser.add_argument("--output", type=Path, help="путь к итоговому файлу")
    parser.add_argument("--no-images", action="store_true", help="не скачивать иллюстрации")
    parser.add_argument(
        "--preset",
        default=None,
        help=(
            f"пресет сжатия: {', '.join(preset_names())} "
            f"(по умолчанию {DEFAULT_PRESET}); несовместим с ручными параметрами сжатия"
        ),
    )
    parser.add_argument(
        "--max-image-mb",
        default=None,
        help=f"порог сжатия в МБ, 0 отключает сжатие (по умолчанию {DEFAULT_MAX_MB})",
    )
    parser.add_argument(
        "--max-image-width",
        default=None,
        help=f"максимальная ширина изображения (по умолчанию {DEFAULT_MAX_WIDTH})",
    )
    parser.add_argument(
        "--quality",
        default=None,
        help=f"качество JPEG, 1-100 (по умолчанию {DEFAULT_QUALITY})",
    )
    parser.add_argument(
        "--charset",
        default=DEFAULT_PROFILE,
        help=f"профиль набора символов: {', '.join(PROFILES)} (по умолчанию {DEFAULT_PROFILE})",
    )
    parser.add_argument("--team", help="имя команды-переводчика")
    parser.add_argument("--chapters", help="выборка глав: 1-50, 1,3,5, v2")
    parser.add_argument(
        "--rate-limit",
        default=DEFAULT_RATE_LIMIT,
        help=f"запросов в секунду (по умолчанию {DEFAULT_RATE_LIMIT})",
    )
    parser.add_argument("--retries", default=DEFAULT_RETRIES, help="число попыток")
    parser.add_argument("--no-tui", action="store_true", help="без интерактивного интерфейса")
    parser.add_argument("--title", help="название книги (dc:title)")
    parser.add_argument("--author", help="автор (dc:creator)")
    parser.add_argument("--description", help="описание (dc:description)")
    parser.add_argument(
        "--language",
        help=f"код языка: {', '.join(LANGUAGE_CODES)} (по умолчанию язык сайта)",
    )
    parser.add_argument("--subjects", help="жанры через запятую (dc:subject)")
    parser.add_argument("--date", help="дата в формате YYYY-MM-DD (dc:date)")
    parser.add_argument("--publisher", help="издатель (dc:publisher)")
    parser.add_argument("--series", help="название серии")
    parser.add_argument("--series-index", help="номер тома в серии")
    parser.add_argument("--cover", help="идентификатор обложки из `--list-covers`")
    parser.add_argument("--no-cover", action="store_true", help="собрать EPUB без обложки")
    parser.add_argument(
        "--list-covers", action="store_true", help="показать доступные обложки и завершиться"
    )
    return parser


def parse_args(argv: list[str] | None = None) -> Options:
    """Разбирает аргументы; некорректное значение даёт `ArgumentError` (13.1)."""
    parser = build_parser()
    namespace = parser.parse_args(argv)

    # argparse проглатывает исключение из `type=` и заменяет своим текстом, поэтому
    # проверки значений выполняются здесь: сообщение остаётся информативным.
    _validate(namespace)

    max_image_mb = (
        positive_float(str(namespace.max_image_mb))
        if namespace.max_image_mb is not None
        else None
    )
    max_image_width = (
        positive_int(str(namespace.max_image_width))
        if namespace.max_image_width is not None
        else None
    )
    quality = jpeg_quality(str(namespace.quality)) if namespace.quality is not None else None
    rate_limit = positive_float(str(namespace.rate_limit))
    retries = positive_int(str(namespace.retries))
    if namespace.chapters is not None:
        parse_chapter_selection(namespace.chapters)
    try:
        resolved = resolve_settings(
            namespace.preset,
            max_width=max_image_width,
            quality=quality,
            max_mb=max_image_mb,
        )
    except PresetError as error:
        raise ArgumentError(str(error)) from error
    return Options(
        slug_url=namespace.slug_url,
        output=namespace.output,
        include_images=not namespace.no_images,
        max_image_mb=resolved.max_mb,
        max_image_width=resolved.max_width,
        quality=resolved.quality,
        grayscale=resolved.grayscale,
        preset=resolved.preset,
        charset=namespace.charset,
        team=namespace.team,
        chapters=namespace.chapters,
        rate_limit=rate_limit,
        retries=retries,
        no_tui=namespace.no_tui,
        title=namespace.title,
        author=namespace.author,
        description=namespace.description,
        language=namespace.language,
        subjects=namespace.subjects,
        date=namespace.date,
        publisher=namespace.publisher,
        series=namespace.series,
        series_index=(
            positive_int(namespace.series_index) if namespace.series_index is not None else None
        ),
        cover_id=positive_int(namespace.cover) if namespace.cover is not None else None,
        cover_disabled=namespace.no_cover,
        list_covers=namespace.list_covers,
    )


def default_filename(book: Book) -> str:
    """Имя файла из русского названия, иначе из оригинального (13.5)."""
    stem = sanitize_name(book.rus_name or book.name or "book")
    return f"{stem}{DEFAULT_OUTPUT_SUFFIX}"


def exit_code_for(error: Exception | None) -> int:
    """Код возврата: 0 при успехе, 2 при ошибке значения, 1 при сбое сборки (13.4)."""
    if error is None:
        return 0
    if isinstance(error, ArgumentError):
        return 2
    return 1
