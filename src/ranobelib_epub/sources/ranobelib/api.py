"""Модуль-источник RanobeLib: реализация контракта `BookSource`.

Модуль владеет всеми данными своего сайта: базовый адрес API, обязательные заголовки,
origin и заголовки CDN для иллюстраций. Остальные слои получают уже разобранные модели
и не знают ни про HTTP, ни про формат ответов Mangalib API.
"""

from __future__ import annotations

from typing import Any

import httpx

from ...models import Book, Chapter, ChapterContent, Cover, Report
from ..base import SourceConfig
from .age import confirm_age, requires_confirmation
from .client import (
    ApiError,
    ApiUnavailableError,
    AuthorizationRequiredError,
    BookNotFoundError,
    ClientConfig,
    MissingParameterError,
    RanobeLibClient,
    WafBlockedError,
)
from .media import IMAGE_HEADERS, SITE_ORIGIN
from .parsing import parse_book, parse_chapter_content, parse_chapters, parse_covers
from .url import parse_book_url

BOOK_PATH = "/manga/{slug_url}"
CHAPTERS_PATH = "/manga/{slug_url}/chapters"
CHAPTER_PATH = "/manga/{slug_url}/chapter"
COVERS_PATH = "/manga/{slug_url}/covers"

#: Карточка без расширения полей не отдаёт автора, жанров и описания (решение 5
#: design.md): они приходят только с `fields[]=authors&fields[]=genres&fields[]=summary`.
BOOK_FIELDS: dict[str, list[str]] = {"fields[]": ["authors", "genres", "summary"]}

#: Развёрнутая причина главы без выбранной ветки перевода.
NO_BRANCH_REASON = (
    "у главы нет ветки перевода: ни одна ветка не выбрана или недоступна; "
    "обход доступа не выполняется"
)


def matches(url: str) -> bool:
    """Источник RanobeLib поддерживает ссылки на `ranobelib.me`."""
    return "ranobelib.me" in (url or "").lower()


def parse(url: str) -> str:
    """Разбирает ссылку RanobeLib в `slug_url` без сетевых запросов."""
    return parse_book_url(url)


def create(config: SourceConfig) -> RanobeLibSource:
    """Собирает рантайм-объект источника с общими сетевыми параметрами."""
    client = RanobeLibClient(ClientConfig(rate_limit=config.rate_limit, retries=config.retries))
    return RanobeLibSource(client)


def chapter_unavailable_reason(chapter: Chapter) -> str | None:
    """Статическая причина недоступности главы, известная до сетевого запроса.

    Проверяется платный доступ (`expired`/`expired_type`). Отдельного обхода нет: глава
    с истёкшим доступом помечается и в отчёт, минуя любые попытки повторного доступа
    (задача 4.6).
    """
    if getattr(chapter, "expired_type", None):
        return (
            f"доступ к главе ограничен на стороне сайта "
            f"(expired_type={chapter.expired_type}); обход авторизации не выполняется"
        )
    return None


def describe_failure(error: Exception, *, ref: str = "") -> str:
    """Человекочитаемая причина сбоя получения главы (решение 3).

    Классификация живёт в модуле-источнике, потому что распознавание HTTP-ошибок
    требует знания `httpx` и классов клиента; иначе `pipeline` начал бы зависеть от HTTP.
    `ref` — ссылка книги: по ней восстанавливается эндпоинт для сообщения.
    """
    endpoint = CHAPTER_PATH.format(slug_url=ref) if ref else ""
    target = f" по адресу {endpoint}" if endpoint else ""

    if isinstance(error, httpx.TimeoutException):
        return f"таймаут запроса{target}: сервер не ответил вовремя"
    if isinstance(error, httpx.TransportError):
        return f"сетевая ошибка{target}: {error}"
    if isinstance(error, WafBlockedError):
        return "отказ защиты (403 с HTML-телом): проверьте заголовки Site-Id/Origin/Referer"
    if isinstance(error, AuthorizationRequiredError):
        return "глава требует авторизации; обход доступа не выполняется"
    if isinstance(error, BookNotFoundError):
        return f"глава не найдена (404){target}"
    if isinstance(error, MissingParameterError):
        return f"не передан обязательный параметр запроса (422){target}"
    if isinstance(error, ApiUnavailableError):
        return f"исчерпаны повторы: {error}"
    if isinstance(error, ApiError):
        message = str(error).strip() or type(error).__name__
        return f"некорректный ответ API: {message}"

    message = str(error).strip()
    name = type(error).__name__
    return f"{name}: {message}" if message else name


def is_authorization_error(exc: Exception) -> bool:
    """Отказ по причине авторизации — повторять его нельзя (решение 11)."""
    return isinstance(exc, AuthorizationRequiredError)


class RanobeLibSource:
    """Реализация контракта `BookSource` для сайта `ranobelib.me`."""

    name = "ranobelib"

    def __init__(self, client: RanobeLibClient) -> None:
        self.client = client

    def parse_url(self, url: str) -> str:
        return parse_book_url(url)

    def resource_url(self, url: str) -> str:
        """Абсолютный URL ресурса: относительные пути приписываются к origin сайта."""
        if not url:
            return ""
        if url.startswith(("http://", "https://")):
            return url
        return f"{SITE_ORIGIN.rstrip('/')}/{url.lstrip('/')}"

    async def fetch_resource(self, url: str) -> bytes:
        """Скачивает бинарный ресурс с заголовками CDN своего сайта."""
        return await self.client.get_bytes(self.resource_url(url), headers=IMAGE_HEADERS)

    async def fetch_book(self, ref: str) -> Book:
        payload = await self.client.get_json(BOOK_PATH.format(slug_url=ref), dict(BOOK_FIELDS))
        return parse_book(payload)

    async def fetch_chapters(self, ref: str) -> list[Chapter]:
        """Весь список глав одним запросом: пагинации на этом эндпоинте нет."""
        payload = await self.client.get_json(CHAPTERS_PATH.format(slug_url=ref))
        return parse_chapters(payload)

    async def fetch_covers(self, ref: str) -> tuple[Cover, ...]:
        """Список обложек карусели; недоступность не прерывает сборку.

        Пустой список возвращается и при ошибке эндпоинта, и при отсутствии обложек —
        интерфейс показывает только «обложку по умолчанию» и «без обложки» (решение 2).
        """
        try:
            payload = await self.client.get_json(COVERS_PATH.format(slug_url=ref))
        except ApiError:
            return ()
        return parse_covers(payload)

    async def fetch_chapter_content(
        self,
        ref: str,
        chapter: Chapter,
        branch_id: int,
    ) -> ChapterContent:
        """Текст главы конкретной ветки перевода.

        `volume` обязателен: без него сервер отвечает `422`.
        """
        payload = await self.client.get_json(
            CHAPTER_PATH.format(slug_url=ref),
            {"volume": chapter.volume, "number": chapter.number, "branch_id": branch_id},
        )
        return parse_chapter_content(
            payload,
            volume=chapter.volume,
            number=chapter.number,
        )

    def chapter_unavailable_reason(self, chapter: Chapter) -> str | None:
        return chapter_unavailable_reason(chapter)

    def describe_failure(self, error: Exception, *, ref: str = "") -> str:
        return describe_failure(error, ref=ref)

    def is_authorization_error(self, error: Exception) -> bool:
        return is_authorization_error(error)

    def requires_age_confirmation(self, book: Book) -> bool:
        return requires_confirmation(book)

    def confirm_age(self, book: Book, report: Report | None = None) -> str:
        return confirm_age(book, report)

    async def aclose(self) -> None:
        await self.client.aclose()


def raw_data(payload: Any) -> dict[str, Any]:
    """Доступ к сырому `data` ответа — нужен только для диагностики и тестов."""
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
    return {}
