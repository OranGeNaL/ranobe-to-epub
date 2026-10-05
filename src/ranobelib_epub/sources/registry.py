"""Реестр модулей-источников и выбор источника по пользовательской ссылке."""

from __future__ import annotations

from .base import (
    BookSource,
    SourceConfig,
    SourceModule,
    UnsupportedSourceError,
)


class SourceRegistry:
    """Упорядоченный набор модулей-источников.

    Порядок регистрации задаёт приоритет: если несколько источников заявляют одну
    ссылку, выбирается зарегистрированный раньше.
    """

    def __init__(self) -> None:
        self._modules: list[SourceModule] = []

    def register(self, module: SourceModule) -> None:
        self._modules.append(module)

    @property
    def modules(self) -> tuple[SourceModule, ...]:
        return tuple(self._modules)

    def resolve(self, url: str) -> SourceModule:
        """Модуль, поддерживающий ссылку; иначе `UnsupportedSourceError` (без сети)."""
        for module in self._modules:
            if module.matches(url):
                return module
        available = ", ".join(module.name for module in self._modules) or "нет"
        raise UnsupportedSourceError(
            f"Ссылка не поддерживается ни одним источником. Доступные источники: {available}"
        )

    def create(self, url: str, config: SourceConfig | None = None) -> BookSource:
        """Создаёт рантайм-объект источника, выбранного по ссылке."""
        return self.resolve(url).create(config or SourceConfig())


_default_registry = SourceRegistry()


def default_registry() -> SourceRegistry:
    """Реестр по умолчанию: в него регистрируются штатные модули-источники."""
    return _default_registry
