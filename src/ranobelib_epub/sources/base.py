"""Единый контракт модулей взаимодействия с сайтами-источниками книг.

`BookSource` — рантайм-объект, которым пользуются все остальные слои. Он оперирует
только доменными моделями из `ranobelib_epub.models` и не раскрывает HTTP-клиент,
заголовки или формат ответов конкретного сайта.

`SourceModule` описывает источник до его создания: как узнать «свою» ссылку, как
разобрать её в ссылку книги и как собрать рантайм-объект под нужную конфигурацию.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Protocol, runtime_checkable

from ..models import Book, Chapter, ChapterContent, Cover, Report

#: Троттлинг по умолчанию (запросов в секунду) и число повторов.
DEFAULT_RATE_LIMIT = 4.0
DEFAULT_RETRIES = 3


class SourceError(RuntimeError):
    """Базовая ошибка источника книги."""


class NotFoundError(SourceError):
    """Запрошенный ресурс (книга или глава) не найден на сайте."""


class UnsupportedSourceError(SourceError):
    """Ни один зарегистрированный источник не поддерживает ссылку."""


class InvalidBookUrlError(SourceError, ValueError):
    """Ссылка не указывает на книгу.

    Наследуется и от `ValueError`, чтобы прежний контракт разбора ссылки сохранился,
    и от `SourceError`, чтобы вызывающий код мог ловить ошибки источника единообразно.
    """


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """Сетевые параметры, общие для любого источника."""

    rate_limit: float = DEFAULT_RATE_LIMIT
    retries: int = DEFAULT_RETRIES


@runtime_checkable
class BookSource(Protocol):
    """Операции, которые обязан предоставлять любой модуль-источник."""

    name: ClassVar[str]

    def parse_url(self, url: str) -> str:
        """Разбирает пользовательскую ссылку в ссылку книги (без сети)."""
        ...

    async def fetch_book(self, ref: str) -> Book: ...

    async def fetch_chapters(self, ref: str) -> list[Chapter]: ...

    async def fetch_covers(self, ref: str) -> tuple[Cover, ...]: ...

    async def fetch_chapter_content(
        self,
        ref: str,
        chapter: Chapter,
        branch_id: int,
    ) -> ChapterContent: ...

    def resource_url(self, url: str) -> str:
        """Абсолютный URL ресурса по возможно относительной ссылке из данных книги."""
        ...

    async def fetch_resource(self, url: str) -> bytes:
        """Скачивает бинарный ресурс, применяя заголовки и origin своего сайта."""
        ...

    def chapter_unavailable_reason(self, chapter: Chapter) -> str | None:
        """Статическая причина недоступности главы, известная до сетевого запроса."""
        ...

    def describe_failure(self, error: Exception, *, ref: str = "") -> str:
        """Преобразует сбой в человекочитаемую причину; `ref` уточняет эндпоинт."""
        ...

    def is_authorization_error(self, error: Exception) -> bool:
        """Отказ по причине авторизации — необратимый, повторять его нельзя."""
        ...

    def requires_age_confirmation(self, book: Book) -> bool:
        """Требуется ли автоматическое подтверждение возрастного ограничения."""
        ...

    def confirm_age(self, book: Book, report: Report | None = None) -> str:
        """Подтверждает возрастное ограничение и возвращает описание для отчёта."""
        ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SourceModule:
    """Описатель источника: выбор по ссылке, разбор и фабрика рантайм-объекта."""

    name: str
    matches: Callable[[str], bool]
    parse: Callable[[str], str]
    create: Callable[[SourceConfig], BookSource]
