"""Фасад обращения к API: три эндпоинта поверх `RanobeLibClient` (задачи 4.1-4.6)."""

from __future__ import annotations

from typing import Any

from ..models import Book, Chapter, ChapterContent
from .client import AuthorizationRequiredError, RanobeLibClient
from .parsing import parse_book, parse_chapter_content, parse_chapters

BOOK_PATH = "/manga/{slug_url}"
CHAPTERS_PATH = "/manga/{slug_url}/chapters"
CHAPTER_PATH = "/manga/{slug_url}/chapter"


def unavailable_reason(chapter: Chapter) -> str | None:
    """Статическая причина недоступности главы, известная до сетевого запроса.

    Проверяется платный доступ (`expired`/`expired_type`). Отдельного обхода нет: глава
    с истёкшим доступом помечается и в отчёт, минуя любые попытки повторного доступа
    (задача 4.6).
    """
    if getattr(chapter, "expired_type", None):
        return f"глава недоступна: expired_type={chapter.expired_type}"
    return None


def is_authorization_error(exc: Exception) -> bool:
    """Отказ по причине авторизации — повторять его нельзя (решение 11)."""
    return isinstance(exc, AuthorizationRequiredError)


class RanobeLibSource:
    """Три вызова API, которые нужны сборке, и ничего сверх того."""

    def __init__(self, client: RanobeLibClient) -> None:
        self.client = client

    async def fetch_book(self, slug_url: str) -> Book:
        payload = await self.client.get_json(BOOK_PATH.format(slug_url=slug_url))
        return parse_book(payload)

    async def fetch_chapters(self, slug_url: str) -> list[Chapter]:
        """Весь список глав одним запросом: пагинации на этом эндпоинте нет."""
        payload = await self.client.get_json(CHAPTERS_PATH.format(slug_url=slug_url))
        return parse_chapters(payload)

    async def fetch_chapter_content(
        self,
        slug_url: str,
        chapter: Chapter,
        branch_id: int,
    ) -> ChapterContent:
        """Текст главы конкретной ветки перевода.

        `volume` обязателен: без него сервер отвечает `422`.
        """
        payload = await self.client.get_json(
            CHAPTER_PATH.format(slug_url=slug_url),
            {"volume": chapter.volume, "number": chapter.number, "branch_id": branch_id},
        )
        return parse_chapter_content(
            payload,
            volume=chapter.volume,
            number=chapter.number,
        )


def raw_data(payload: Any) -> dict[str, Any]:
    """Доступ к сырому `data` ответа — нужен только для диагностики и тестов."""
    if isinstance(payload, dict):
        data = payload.get("data")
        return data if isinstance(data, dict) else {}
    return {}