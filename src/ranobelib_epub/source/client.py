"""HTTP-клиент к Mangalib API (решение 1).

Обязательные заголовки `Site-Id`/`Origin`/`Referer` проверялись перебором: без них nginx
отдаёт `403` с HTML-телом даже при браузерном `User-Agent`. Значения вынесены в
`ClientConfig`, потому что на том же хосте живут другие площадки с другим `Site-Id`.

Основа — `httpx.AsyncClient`: по решению 8 сеть выполняется в `asyncio`, а параллельная
загрузка глав (§12) строится поверх этого же клиента.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ..models import DEFAULT_API_BASE, DEFAULT_SITE_ID, DEFAULT_SITE_ORIGIN

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_RATE_LIMIT = 4.0


class ApiError(RuntimeError):
    """Базовая ошибка обращения к API."""


class WafBlockedError(ApiError):
    """Ответ `403` с текстовым (HTML) телом — сработала защита, а не ошибка данных.

    Сообщение намеренно указывает на заголовки: причина отказа — их отсутствие, а не
    параметры запроса, иначе диагностика уводит в сторону.
    """


class BookNotFoundError(ApiError):
    """Ответ `404` — книги или главы нет."""


class MissingParameterError(ApiError):
    """Ответ `422` — не передан обязательный параметр запроса."""


class ApiUnavailableError(ApiError):
    """Попытки исчерпаны: временная ошибка не разрешилась после всех повторов."""


class AuthorizationRequiredError(ApiError):
    """Контент требует учётной записи.

    Обход не предпринимается (решение 11): это настоящий контроль доступа, в отличие
    от клиентской декларации возраста.
    """


class RateLimiter:
    """Ограничитель скорости: не более `rate` запросов в секунду.

    Пауза считается от последнего *старта* запроса, а не от его окончания, иначе при
    быстрых ответах фактическая частота оказывалась бы выше лимита.
    """

    def __init__(self, rate: float = DEFAULT_RATE_LIMIT) -> None:
        self.rate = float(rate)
        self._interval = 1.0 / self.rate if self.rate > 0 else 0.0
        self._last_start = 0.0
        self._lock = asyncio.Lock()

    @property
    def interval(self) -> float:
        return self._interval

    async def acquire(self) -> float:
        """Дожидается своей очереди и возвращает фактическую паузу в секундах."""
        if self._interval <= 0:
            return 0.0
        async with self._lock:
            wait = max(0.0, self._last_start + self._interval - time.monotonic())
            if wait:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()
            return wait


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Конфигурация обращения к API."""

    base_url: str = DEFAULT_API_BASE
    site_id: int = DEFAULT_SITE_ID
    origin: str = DEFAULT_SITE_ORIGIN
    rate_limit: float = DEFAULT_RATE_LIMIT
    retries: int = 3
    timeout: float = 30.0
    backoff: float = 0.5

    def headers(self) -> dict[str, str]:
        return {
            "Site-Id": str(self.site_id),
            "Origin": self.origin,
            "Referer": f"{self.origin}/",
        }


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


class RanobeLibClient:
    """Клиент API с повторами, троттлингом и разбором ошибок.

    Открывается лениво, чтобы создание клиента не требовало готового event loop, и
    закрывается через `aclose()` либо контекстный менеджер.
    """

    def __init__(
        self,
        config: ClientConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config or ClientConfig()
        self.rate_limiter = RateLimiter(self.config.rate_limit)
        self._transport = transport
        self._sleep = sleep or asyncio.sleep
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                headers=self.config.headers(),
                timeout=self.config.timeout,
                transport=self._transport,
                follow_redirects=True,
            )
        return self._client

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._client.is_closed

    async def aclose(self) -> None:
        """Освобождает соединения (сценарий «Прерывание пользователем освобождает ресурсы»)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> RanobeLibClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Выполняет GET и возвращает распарсенный JSON."""
        response = await self._request(path, params)
        return _parse_json(response, path)

    async def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        """Скачивает бинарный файл по абсолютному URL (иллюстрации).

        Запрос идёт мимо `base_url` хоста API: файлы лежат на origin сайта и требуют
        `Referer` с браузерным `User-Agent`, иначе CDN отвечает 403, а путь на хосте
        API отдаёт 404. Ретрои и троттлинг те же, что у JSON-запросов.
        """
        attempts = max(1, self.config.retries)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            await self.rate_limiter.acquire()
            try:
                response = await self.client.get(url, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
            else:
                if response.status_code not in RETRY_STATUSES:
                    _raise_for_status(response, url)
                    return response.content

            if attempt < attempts:
                await self._sleep(self.config.backoff * (2 ** (attempt - 1)))

        raise ApiUnavailableError(
            f"Не удалось скачать {url} после {attempts} попыток"
            + (f": {last_error}" if last_error else "")
        )

    async def _request(self, path: str, params: dict[str, Any] | None) -> httpx.Response:
        attempts = max(1, self.config.retries)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            await self.rate_limiter.acquire()
            try:
                response = await self.client.get(path, params=params)
            except httpx.TimeoutException as exc:
                last_error = exc
                response = None  # type: ignore[assignment]
            else:
                # Ошибки данных разбираются сразу: повтор их не исправит.
                if response.status_code not in RETRY_STATUSES:
                    _raise_for_status(response, path)
                    return response

            if attempt < attempts:
                await self._sleep(self.config.backoff * (2 ** (attempt - 1)))

        raise ApiUnavailableError(
            f"Не удалось получить {path} после {attempts} попыток"
            + (f": {last_error}" if last_error else "")
        )


def _raise_for_status(response: httpx.Response, path: str) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return

    text = ""
    with contextlib.suppress(Exception):  # pragma: no branch - тело может быть недоступно
        text = response.text

    if status == 403 and _looks_like_html(text):
        raise WafBlockedError(
            f"Запрос {path} отклонён защитой (403 без JSON). Проверьте заголовки "
            "Site-Id/Origin/Referer в конфигурации клиента"
        )
    if status == 404:
        raise BookNotFoundError(f"Не найдено: {path} (404)")
    if status == 422:
        raise MissingParameterError(f"Отсутствует обязательный параметр запроса {path} (422)")
    if status in (401, 403) or _mentions_authorization(text):
        raise AuthorizationRequiredError(f"Контент требует авторизации: {path} ({status})")

    raise ApiError(f"Неожиданный ответ на {path}: {status}")


def _mentions_authorization(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("not authorized", "notauthorized", "authorization", "login", "sign in")
    )


def _parse_json(response: httpx.Response, path: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise ApiError(f"Ответ на {path} не является JSON: {exc}") from exc