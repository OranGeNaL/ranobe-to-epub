"""Слой доступа к сайту `ranobelib.me`.

Единственная часть проекта, которая знает про HTTP и формат ответов Mangalib API.
Остальные слои принимают и возвращают модели из `ranobelib_epub.models`.
"""

from __future__ import annotations

from .url import InvalidBookUrlError, parse_book_id, parse_book_url

__all__ = ["InvalidBookUrlError", "parse_book_id", "parse_book_url"]
