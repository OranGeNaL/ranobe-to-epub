"""Разбор пользовательской ссылки на книгу (решение 2).

Чистая функция над URL без сети: `slug_url` уже содержится в ссылке, поэтому отдельный
поиск книги не нужен, а функция тестируется без фикстур.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..base import InvalidBookUrlError
from .media import SITE_ORIGIN

# `{id}--{slug}`: числовой идентификатор, двудефис, человекочитаемый слаг.
_SLUG_RE = re.compile(r"^(?P<book_id>\d+)--(?P<slug>[^/?#]+)$")
_BOOK_PATH_RE = re.compile(r"/book/(?P<slug_url>\d+--[^/?#]+)")

__all__ = ["InvalidBookUrlError", "parse_book_id", "parse_book_url"]


def parse_book_url(url: str) -> str:
    """Извлекает `slug_url` вида `{id}--{slug}` из ссылки на книгу.

    Отбрасывает query-параметры (включая `?section=chapters`), завершающий слеш и
    регистр хоста. Хост не проверяется: ссылки вида `ranobelib.me`, другого регистра и
    даже зеркала дают один и тот же `slug_url`, а отсечение по хосту заставило бы
    отвергать рабочие ссылки из закладок.
    """
    if not url or not url.strip():
        raise InvalidBookUrlError("Пустая ссылка: ожидалась ссылка на книгу")

    candidate = url.strip()
    if _SLUG_RE.match(candidate.rstrip("/")):
        # Пользователь вставил сам `slug_url` без ссылки — принимаем как есть.
        return candidate.rstrip("/")

    if "://" not in candidate:
        candidate = f"https://{candidate.lstrip('/')}"

    parts = urlsplit(candidate)
    path = parts.path
    match = _BOOK_PATH_RE.search(path)
    if match is None:
        raise InvalidBookUrlError(
            f"Ссылка {url!r} не указывает на книгу: ожидался путь вида "
            f"{SITE_ORIGIN}/book/{{id}}--{{slug}}"
        )

    slug_url = match.group("slug_url").rstrip("/")
    if not _SLUG_RE.match(slug_url):
        raise InvalidBookUrlError(
            f"Ссылка {url!r} не указывает на книгу: в пути нет пары {{id}}--{{slug}}"
        )

    return slug_url


def parse_book_id(url: str) -> int:
    """Числовой идентификатор книги из той же ссылки (только для метаданных)."""
    slug_url = parse_book_url(url)
    return int(_SLUG_RE.match(slug_url).group("book_id"))  # type: ignore[union-attr]
