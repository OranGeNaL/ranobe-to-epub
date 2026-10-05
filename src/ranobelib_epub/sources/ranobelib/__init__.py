"""Модуль-источник RanobeLib: реализация контракта и регистрация в реестре.

Импорт этого пакета регистрирует RanobeLib в реестре источников по умолчанию. Все
site-specific данные (адрес API, заголовки, origin, политика возраста, выбор перевода)
остаются внутри пакета.
"""

from __future__ import annotations

from ..base import SourceModule
from ..registry import default_registry
from .api import (
    NO_BRANCH_REASON,
    RanobeLibSource,
    chapter_unavailable_reason,
    create,
    describe_failure,
    is_authorization_error,
    matches,
    parse,
)
from .url import InvalidBookUrlError, parse_book_id, parse_book_url

MODULE = SourceModule(
    name="ranobelib",
    matches=matches,
    parse=parse,
    create=create,
)


def register() -> None:
    """Регистрирует модуль RanobeLib в реестре по умолчанию."""
    default_registry().register(MODULE)


register()

__all__ = [
    "MODULE",
    "NO_BRANCH_REASON",
    "InvalidBookUrlError",
    "RanobeLibSource",
    "chapter_unavailable_reason",
    "create",
    "describe_failure",
    "is_authorization_error",
    "matches",
    "parse",
    "parse_book_id",
    "parse_book_url",
    "register",
]
