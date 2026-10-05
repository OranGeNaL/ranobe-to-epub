"""Модели предметной области.

Единственный контракт между слоями источников (`sources`), сборкой EPUB (`epub`,
`prosemirror`, `images`, `charset`) и интерфейсом (`tui`, `cli`). Ни одна из этих частей
не должна импортировать конкретный модуль-источник, кроме моделей и контракта.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

#: Фиксированный набор языков для метаданных (решение 6 design.md).
LANGUAGE_CODES: tuple[str, ...] = ("ru", "en", "ja", "zh", "ko", "de", "fr", "es", "it", "pt")

#: Человекочитаемые названия языков для интерактивных выборов.
LANGUAGE_LABELS: dict[str, str] = {
    "ru": "Русский",
    "en": "Английский",
    "ja": "Японский",
    "zh": "Китайский",
    "ko": "Корейский",
    "de": "Немецкий",
    "fr": "Французский",
    "es": "Испанский",
    "it": "Итальянский",
    "pt": "Португальский",
}


@dataclass(frozen=True, slots=True)
class Book:
    """Метаданные книги.

    Обязателен только `slug_url` — идентичность книги, из которой выведены остальные
    поля. Всё остальное опционально: сайт отдаёт разнородные наборы полей, и отсутствие
    любого из них не должно останавливать сборку (требование «Получение метаданных
    книги»).
    """

    slug_url: str
    book_id: int | None = None
    name: str | None = None
    rus_name: str | None = None
    author: str | None = None
    cover: str | None = None
    status: str | None = None
    release_date: str | None = None
    year: int | None = None
    genres: tuple[str, ...] = ()
    summary: str | None = None
    in_language: str | None = None
    age_restriction_id: int | None = None
    age_restriction_label: str | None = None
    publisher: str | None = None
    series: str | None = None
    series_index: int | None = None

    @property
    def title(self) -> str:
        """Название для OPF и имени файла: русское при наличии, иначе оригинальное."""
        return self.rus_name or self.name or self.slug_url


@dataclass(frozen=True, slots=True)
class Cover:
    """Обложка из карусели томов (`GET /manga/{slug_url}/covers`)."""

    id: int
    order: int
    label: str
    url: str | None


@dataclass(frozen=True, slots=True)
class MetadataOverrides:
    """Переопределения метаданных (решение 1 design.md).

    Поле со значением `None`/пустой строкой означает «использовать значение из данных
    сайта»; серия переопределяется только вместе с `series`/`series_index`.
    """

    title: str | None = None
    author: str | None = None
    description: str | None = None
    language: str | None = None
    genres: tuple[str, ...] | None = None
    date: str | None = None
    publisher: str | None = None
    series: str | None = None
    series_index: int | None = None


def apply_overrides(book: Book, overrides: MetadataOverrides) -> Book:
    """Применяет переопределения к `Book`, не трогая незаполненные поля.

    Название записывается в `rus_name`, потому что `Book.title` и имя файла по умолчанию
    берутся именно от него; дальше ниже по потоку всё уже читает `Book`, и отдельных
    каналов для переопределений не требуется.
    """
    kwargs: dict[str, Any] = {}
    if overrides.title:
        kwargs["rus_name"] = overrides.title
    if overrides.author:
        kwargs["author"] = overrides.author
    if overrides.description:
        kwargs["summary"] = overrides.description
    if overrides.language:
        kwargs["in_language"] = overrides.language
    if overrides.genres:
        kwargs["genres"] = overrides.genres
    if overrides.publisher:
        kwargs["publisher"] = overrides.publisher
    if overrides.series:
        kwargs["series"] = overrides.series
    if overrides.series_index is not None:
        kwargs["series_index"] = overrides.series_index
    return replace(book, **kwargs)


def resolve_cover_url(
    book: Book,
    covers: Iterable[Cover] = (),
    cover_id: int | None = None,
    cover_disabled: bool = False,
) -> str | None:
    """URL обложки для сборки: отказ → без обложки, выбор → из карусели, иначе с сайта.

    Если `cover_id` задан, но в карусели его нет — возвращается `None` (обложку считать
    недоступной, а причину фиксирует вызывающий код).
    """
    if cover_disabled:
        return None
    if cover_id is not None:
        for cover in covers or ():
            if cover.id == cover_id:
                return cover.url
        return None
    return book.cover


@dataclass(frozen=True, slots=True)
class TranslationBranch:
    """Ветка перевода главы."""

    id: int
    name: str | None = None
    teams: tuple[str, ...] = ()


@dataclass(slots=True)
class Chapter:
    """Глава книги.

    `number` хранится дословно строкой: сайт отдаёт дробные значения (`22.5`) для
    побочных историй, и любое приведение к `int` потеряло бы уровень иерархии.
    `label` — вычисленный номер `{volume}.{number}`, назначается функцией
    `ranobelib_epub.numbering.assign_labels` с учётом коллизий.
    """

    id: int
    volume: int
    number: str
    name: str
    branches: tuple[TranslationBranch, ...] = ()
    branch_id: int | None = None
    number_secondary: int | None = None
    expired_type: int | None = None
    label: str | None = None

    @property
    def sort_key(self) -> tuple[int, ...]:
        """Числовой ключ иерархической сортировки: том, затем все части номера."""
        return number_sort_key(self.volume, self.number)

    @property
    def default_branch(self) -> TranslationBranch | None:
        """Актуальная ветка — первая в списке (решение 7)."""
        return self.branches[0] if self.branches else None


@dataclass(frozen=True, slots=True)
class Attachment:
    """Вложение главы: картинка приходит отдельным массивом, а не внутри ProseMirror."""

    name: str
    url: str
    extension: str | None = None
    width: int | None = None
    height: int | None = None

    def absolute_url(self, origin: str) -> str:
        """Абсолютный URL: host'ы imglib/cdnlibs для этих путей отдают 404.

        Origin принадлежит сайту-источнику, поэтому передаётся явно, а не берётся из
        общей константы.
        """
        if self.url.startswith(("http://", "https://")):
            return self.url
        return f"{origin.rstrip('/')}{self.url}"


@dataclass(frozen=True, slots=True)
class ChapterContent:
    """Содержимое главы: ProseMirror-JSON плюс вложения."""

    doc: dict[str, Any] | None = None
    attachments: tuple[Attachment, ...] = ()
    branch_id: int | None = None
    volume: int | None = None
    number: str | None = None

    @property
    def available(self) -> bool:
        return self.doc is not None

    def attachment_by_name(self, name: str) -> Attachment | None:
        return next((a for a in self.attachments if a.name == name), None)


def number_sort_key(volume: int | str, number: int | str) -> tuple[int, ...]:
    """Числовой ключ для сортировки глав.

    `22.5` разбирается как `(22, 5)`, поэтому `1.22.5` оказывается потомком `1.22`, а не
    после всех глав тома. Нечисловые хвосты (`22.5a`) уходят в конец как отдельный
    уровень, чтобы порядок оставался определённым. Аргументы приводятся к строке:
    сайт отдаёт `number` то числом, то строкой.
    """
    parts: list[int] = [_digits(volume)]
    for chunk in str(number).split("."):
        parts.append(_digits(chunk))
    return tuple(parts)


def _digits(value: int | str) -> int:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else 0


@dataclass(slots=True)
class UnavailableChapter:
    """Глава, которую получить не удалось."""

    label: str
    volume: int
    number: str
    name: str
    reason: str


@dataclass(slots=True)
class MissingImage:
    """Картинка, которую не удалось получить или встроить."""

    chapter_label: str
    reference: str
    url: str | None
    reason: str


@dataclass(slots=True)
class Report:
    """Единый объект отчёта (решение 10).

    Наполняется всеми частями: слоем сайта, конвертером, загрузчиком картинок,
    фильтром символов и сборщиком EPUB.
    """

    total_chapters: int = 0
    built_chapters: int = 0
    unavailable: list[UnavailableChapter] = field(default_factory=list)
    missing_images: list[MissingImage] = field(default_factory=list)
    unknown_nodes: list[str] = field(default_factory=list)
    removed_characters: int = 0
    chapters_with_removed_characters: int = 0
    age_restriction_id: int | None = None
    age_restriction_label: str | None = None
    age_confirmed_chapters: int = 0
    images_deduplicated: int = 0
    image_bytes_saved: int = 0
    file_size: int | None = None
    output_path: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def age_confirmed(self) -> bool:
        """Подтверждалось ли возрастное ограничение (решение 11)."""
        return self.age_confirmed_chapters > 0

    @property
    def partial(self) -> bool:
        return bool(self.unavailable or self.missing_images or self.removed_characters)
