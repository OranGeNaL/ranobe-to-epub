"""Контрактный тест живого API RanobeLib (задача 15.7).

Один реальный запрос проверяет, что форма ответа не разошлась с фикстурами: тесты на
фикстурах показывают правдоподобный, но устаревший результат, если сайт сменит ключи.

По умолчанию тест пропускается: сеть нужна только для осознанной проверки контракта.
Запуск: ``RANOBELIB_CONTRACT=1 uv run pytest -m contract``.
"""

from __future__ import annotations

import os

import httpx
import pytest

from ranobelib_epub.source.client import ClientConfig, RanobeLibClient
from ranobelib_epub.source.parsing import parse_book

CONTRACT_ENV = "RANOBELIB_CONTRACT"
SLUG = "94231--rezero-kara-hajimeru-isekai-seikatsu-outo-no-ichinichi-hen"

pytestmark = pytest.mark.contract

requires_live_api = pytest.mark.skipif(
    os.environ.get(CONTRACT_ENV) != "1",
    reason=f"живой API: задайте {CONTRACT_ENV}=1 и запустите с -m contract",
)


@requires_live_api
async def test_book_response_keeps_expected_keys() -> None:
    """Один запрос: набор ключей и успешный разбор того же ответа."""
    config = ClientConfig(rate_limit=0, retries=1, timeout=30.0)

    try:
        async with RanobeLibClient(config) as client:
            payload = await client.get_json(f"/manga/{SLUG}")
    except httpx.TransportError as error:  # нет сети — проверять нечего
        pytest.skip(f"сеть недоступна: {error}")

    data = payload["data"]
    assert {"id", "name", "rus_name", "slug_url", "cover", "status", "ageRestriction"} <= set(data)

    book = parse_book(payload)
    assert book.book_id == 94231
    assert book.rus_name
    assert book.age_restriction_label == "18+"
