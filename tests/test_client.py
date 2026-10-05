"""Тесты HTTP-клиента на подделке `httpx.MockTransport`: сети не требуется."""

from __future__ import annotations

import time
from itertools import pairwise
from typing import Any

import httpx
import pytest

from ranobelib_epub.sources.ranobelib.client import (
    ApiError,
    ApiUnavailableError,
    AuthorizationRequiredError,
    BookNotFoundError,
    ClientConfig,
    MissingParameterError,
    RanobeLibClient,
    WafBlockedError,
)

WAF_HTML = (
    "<!DOCTYPE html>\n<html><head><title>403 Forbidden</title></head><body>blocked</body></html>"
)


def build_client(handler: Any, **config_kwargs: Any) -> RanobeLibClient:
    config = ClientConfig(rate_limit=config_kwargs.pop("rate_limit", 0), **config_kwargs)
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    client = RanobeLibClient(
        config,
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    )
    client.recorded_sleeps = recorded  # type: ignore[attr-defined]
    return client


class TestRequiredHeaders:
    async def test_headers_present_in_request(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"ok": True}})

        async with build_client(handler, retries=1) as client:
            await client.get_json("/manga/94231--x")

        request = seen[0]
        assert request.headers["Site-Id"] == "3"
        assert request.headers["Origin"] == "https://ranobelib.me"
        assert request.headers["Referer"] == "https://ranobelib.me/"
        assert str(request.url).startswith("https://api.cdnlibs.org/api/manga/")

    async def test_site_id_is_configurable(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {}})

        config = ClientConfig(site_id=4, origin="https://anilib.me", rate_limit=0, retries=1)

        async def no_sleep(delay: float) -> None:
            return None

        client = RanobeLibClient(config, transport=httpx.MockTransport(handler), sleep=no_sleep)
        async with client:
            await client.get_json("/manga/x")

        assert seen[0].headers["Site-Id"] == "4"
        assert seen[0].headers["Origin"] == "https://anilib.me"
        assert seen[0].headers["Referer"] == "https://anilib.me/"


class TestErrorClassification:
    async def test_waf_403_html(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text=WAF_HTML)

        async with build_client(handler, retries=1) as client:
            with pytest.raises(WafBlockedError, match="Site-Id/Origin/Referer"):
                await client.get_json("/manga/x")

    async def test_waf_error_mentions_headers_not_data(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text=WAF_HTML)

        async with build_client(handler, retries=1) as client:
            with pytest.raises(WafBlockedError) as info:
                await client.get_json("/manga/x")

        message = str(info.value)
        assert "заголовки" in message
        assert "404" not in message

    async def test_book_not_found_404_json(self) -> None:
        body = {"data": {"toast": {"type": "silent", "message": "Not Found"}}}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json=body)

        async with build_client(handler, retries=1) as client:
            with pytest.raises(BookNotFoundError):
                await client.get_json("/manga/nope")

    async def test_missing_parameter_422(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"errors": {"volume": ["required"]}})

        async with build_client(handler, retries=1) as client:
            with pytest.raises(MissingParameterError) as info:
                await client.get_json("/manga/x/chapter")

        assert "/manga/x/chapter" in str(info.value)

    async def test_authorization_required_is_not_retried(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(401, json={"data": {"message": "Not Authorized"}})

        async with build_client(handler, retries=3) as client:
            with pytest.raises(AuthorizationRequiredError):
                await client.get_json("/manga/x/chapter")

        assert len(calls) == 1, "отказ по авторизации не повторяется (решение 11)"

    async def test_unexpected_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(418, json={"data": {}})

        async with build_client(handler, retries=1) as client:
            with pytest.raises(ApiError, match="418"):
                await client.get_json("/manga/x")

    async def test_non_json_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json at all")

        async with build_client(handler, retries=1) as client:
            with pytest.raises(ApiError, match="не является JSON"):
                await client.get_json("/manga/x")


class TestRetries:
    async def test_retry_then_success(self) -> None:
        statuses = iter([503, 200])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(next(statuses), json={"data": {"content": "ok"}})

        async with build_client(handler, retries=3) as client:
            payload = await client.get_json("/manga/x/chapter")

        assert payload["data"]["content"] == "ok"
        assert client.recorded_sleeps == [0.5], "задержка перед первым повтором"

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    async def test_all_retry_statuses(self, status: int) -> None:
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(status)
            return httpx.Response(status if len(seen) < 2 else 200, json={"data": {}})

        async with build_client(handler, retries=2) as client:
            await client.get_json("/manga/x")

        assert len(seen) == 2

    async def test_timeout_is_retried(self) -> None:
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ReadTimeout("timed out", request=request)
            return httpx.Response(200, json={"data": {}})

        async with build_client(handler, retries=2) as client:
            await client.get_json("/manga/x")

        assert len(attempts) == 2

    async def test_retries_exhausted_raises_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"data": {}})

        async with build_client(handler, retries=3) as client:
            with pytest.raises(ApiUnavailableError, match="3 попыток"):
                await client.get_json("/manga/x")

        assert client.recorded_sleeps == [0.5, 1.0], "нарастающая задержка между попытками"

    async def test_retries_exhausted_reports_last_status(self) -> None:
        seen: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(503)
            return httpx.Response(503, json={"data": {}})

        async with build_client(handler, retries=3) as client:
            with pytest.raises(ApiUnavailableError) as info:
                await client.get_json("/manga/x")

        message = str(info.value)
        assert "3 попыток" in message
        assert "последний статус: 503" in message
        assert len(seen) == 3

    async def test_bytes_retries_exhausted_reports_last_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(504, json={"data": {}})

        async with build_client(handler, retries=2) as client:
            with pytest.raises(ApiUnavailableError) as info:
                await client.get_bytes("https://cdn.example/img.png")

        message = str(info.value)
        assert "2 попыток" in message
        assert "последний статус: 504" in message


class TestThrottling:
    async def test_rate_limit_spaces_requests(self) -> None:
        times: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            times.append(time.monotonic())
            return httpx.Response(200, json={"data": {}})

        config = ClientConfig(rate_limit=4, retries=1, backoff=0)
        client = RanobeLibClient(config, transport=httpx.MockTransport(handler))
        async with client:
            for _ in range(8):
                await client.get_json("/manga/x")

        intervals = [b - a for a, b in pairwise(times)]
        assert all(interval >= 0.2 for interval in intervals), intervals
        assert sum(intervals) >= 1.4, "8 запросов при 4/с требуют больше 1.4 с"

    async def test_zero_rate_limit_does_not_wait(self) -> None:
        handler = lambda r: httpx.Response(200, json={"data": {}})  # noqa: E731
        async with build_client(handler, retries=1) as client:
            assert client.rate_limiter.interval == 0.0
            await client.get_json("/manga/x")

    def test_default_rate_limit_is_four_per_second(self) -> None:
        assert ClientConfig().rate_limit == 4.0
        assert ClientConfig().retries == 3


class TestLifecycle:
    async def test_close_releases_client(self) -> None:
        client = build_client(lambda r: httpx.Response(200, json={"data": {}}), retries=1)
        await client.get_json("/manga/x")
        inner = client._client
        assert inner is not None
        assert client.is_open

        await client.aclose()

        assert client._client is None
        assert inner.is_closed
        assert client.is_open is False

    async def test_context_manager_closes_without_leak(self) -> None:
        handler = lambda r: httpx.Response(200, json={"data": {}})  # noqa: E731
        async with build_client(handler, retries=1) as client:
            await client.get_json("/manga/x")
            inner = client._client

        assert client._client is None
        assert inner.is_closed

    async def test_params_are_passed(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"data": {}})

        async with build_client(handler, retries=1) as client:
            await client.get_json("/manga/x/chapter", {"volume": 1, "number": "22.5"})

        assert "volume=1" in seen[0]
        assert "number=22.5" in seen[0]
