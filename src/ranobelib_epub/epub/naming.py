"""Имена файлов глав внутри EPUB (задача 10.7).

Имя главы — единственное, что читатель видит в списке файлов читалки и в отчёте,
поэтому оно остаётся читаемым: иерархический номер впереди, название следом, а
запрещённые символы заменяются аналогами, а не вырезаются.
"""

from __future__ import annotations

import re
import unicodedata

#: Каталог глав внутри пакета: путь указывается относительно каталога OPF.
TEXT_DIR = "TEXT"

#: Замены символов, которые нельзя оставить в имени файла.
_REPLACEMENTS: dict[str, str] = {
    "/": "-",
    "\\": "-",
    ":": " -",
    "*": "+",
    "?": "",
    '"': "'",
    "'": "'",
    "<": "(",
    ">": ")",
    "|": "-",
}

_FORBIDDEN = re.compile(r'[/\\:*?"<>|]')
_WHITESPACE = re.compile(r"\s+")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f]")

#: Длина названия в имени файла: слишком длинные имена ломают часть читалок.
MAX_NAME_LENGTH = 60


def sanitize(name: str) -> str:
    """Заменяет запрещённые символы, сохраняя читаемость названия."""
    cleaned = _UNSAFE.sub("", name or "")
    cleaned = unicodedata.normalize("NFC", cleaned)
    for forbidden, replacement in _REPLACEMENTS.items():
        cleaned = cleaned.replace(forbidden, replacement)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")
    return cleaned or "Без названия"


def chapter_filename(label: str | None, name: str, index: int, taken: set[str]) -> str:
    """Имя файла главы вида `1.25_Пролог.html` с гарантией уникальности.

    Пробелы заменяются на `_`: в имени записи архива они допустимы, но ссылки на файл
    (`href` в OPF, `src` в NCX) с пробелом не являются допустимым URL, и epubcheck
    отклоняет книгу (`RSC-020`). Коллизии разрешаются суффиксом, а не заменой номера:
    две главы с одинаковым названием получают разные файлы, но одинаковый номер.
    """
    prefix = _stem_part(label) if label else f"{index:04d}"
    stem = f"{prefix}_{_stem_part(name)}"[: MAX_NAME_LENGTH + len(prefix) + 1].strip()

    candidate = f"{stem}.html"
    counter = 2
    while candidate in taken:
        candidate = f"{stem}_{counter}.html"
        counter += 1

    taken.add(candidate)
    return f"{TEXT_DIR}/{candidate}"


def _stem_part(text: str) -> str:
    """Безопасная часть имени файла: пробелы недопустимы в ссылках на файл."""
    return sanitize(text).replace(" ", "_")


def assign_chapter_filenames(chapters: list, labels: dict[int, str] | None = None) -> list[str]:
    """Имена файлов для всех глав в порядке сборки (10.7)."""
    taken: set[str] = set()
    names: list[str] = []
    for index, chapter in enumerate(chapters, start=1):
        label = labels.get(chapter.id) if labels else chapter.label
        names.append(chapter_filename(label, chapter.name, index, taken))
    return names


def is_safe(value: str) -> bool:
    """Нет ли в строке символов, запрещённых в имени файла."""
    return not _FORBIDDEN.search(value) and not _UNSAFE.search(value)
