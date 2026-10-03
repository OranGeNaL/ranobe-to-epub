from __future__ import annotations

from typing import Any

import pytest

from ranobelib_epub.source.url import InvalidBookUrlError, parse_book_id, parse_book_url

SLUG = "94231--rezero-kara-hajimeru-isekai-seikatsu-outo-no-ichinichi-hen"


class FakeClient:
    """Подделка сетевого клиента: любое обращение фиксирует провал теста."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, path: str) -> Any:
        self.calls.append(path)
        raise AssertionError(f"сетевой запрос не должен выполняться: {path}")


class TestParseBookUrl:
    def test_full_url_with_query(self) -> None:
        url = f"https://ranobelib.me/ru/book/{SLUG}?section=chapters"
        assert parse_book_url(url) == SLUG

    def test_trailing_slash(self) -> None:
        assert parse_book_url(f"https://ranobelib.me/ru/book/{SLUG}/") == SLUG

    def test_uppercase_host(self) -> None:
        assert parse_book_url(f"https://RanobeLib.me/ru/book/{SLUG}") == SLUG

    def test_without_scheme(self) -> None:
        assert parse_book_url(f"ranobelib.me/ru/book/{SLUG}") == SLUG

    def test_query_and_hash_are_dropped(self) -> None:
        assert parse_book_url(f"https://ranobelib.me/ru/book/{SLUG}?a=1#x") == SLUG

    def test_slug_only(self) -> None:
        assert parse_book_url(SLUG) == SLUG

    def test_book_id(self) -> None:
        assert parse_book_id(f"https://ranobelib.me/ru/book/{SLUG}") == 94231


class TestParseBookUrlRejects:
    @pytest.mark.parametrize(
        "url",
        [
            "https://ranobelib.me/ru/genre/fantasy",
            "https://ranobelib.me/",
            "",
            "   ",
            "https://ranobelib.me/ru/book/no-numeric-id--slug",
        ],
    )
    def test_invalid_url(self, url: str) -> None:
        with pytest.raises(InvalidBookUrlError):
            parse_book_url(url)

    def test_error_message_mentions_expected_shape(self) -> None:
        with pytest.raises(InvalidBookUrlError, match="book/"):
            parse_book_url("https://ranobelib.me/ru/genre/fantasy")

    def test_no_network_request_on_invalid_url(self) -> None:
        """Задача 2.2: подделка клиента падает при любом обращении к сети."""
        client = FakeClient()

        with pytest.raises(InvalidBookUrlError):
            parse_book_url("https://ranobelib.me/ru/genre/fantasy")

        assert client.calls == []