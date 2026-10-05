"""Публичный API модулей взаимодействия с сайтами-источниками книг.

Потребители (сборка EPUB, TUI, CLI) зависят только от этого пакета: протокола
`BookSource`, конфигурации `SourceConfig` и выбора источника по ссылке. Конкретные
модули (например, `sources.ranobelib`) регистрируются в реестре по умолчанию при
импорте пакета.
"""

from __future__ import annotations

from .base import (
    DEFAULT_RATE_LIMIT,
    DEFAULT_RETRIES,
    BookSource,
    InvalidBookUrlError,
    NotFoundError,
    SourceConfig,
    SourceError,
    SourceModule,
    UnsupportedSourceError,
)
from .registry import SourceRegistry, default_registry

__all__ = [
    "DEFAULT_RATE_LIMIT",
    "DEFAULT_RETRIES",
    "BookSource",
    "InvalidBookUrlError",
    "NotFoundError",
    "SourceConfig",
    "SourceError",
    "SourceModule",
    "SourceRegistry",
    "UnsupportedSourceError",
    "create_source",
    "register_source",
    "resolve_source",
]


def register_source(module: SourceModule) -> None:
    """Добавляет модуль-источник в реестр по умолчанию."""
    default_registry().register(module)


def resolve_source(url: str) -> SourceModule:
    """Модуль-источник, поддерживающий ссылку."""
    return default_registry().resolve(url)


def create_source(url: str, config: SourceConfig | None = None) -> BookSource:
    """Рантайм-объект источника, выбранного по ссылке."""
    return default_registry().create(url, config)


# Подключает штатные модули-источники и регистрирует их в реестре по умолчанию.
from . import ranobelib as _ranobelib  # noqa: E402,F401  (регистрация при импорте)
