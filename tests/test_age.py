"""Тесты подтверждения возрастного ограничения (задачи 6.1-6.5)."""

from __future__ import annotations

import pytest

from ranobelib_epub.models import Book, Report
from ranobelib_epub.source.age import (
    AGE_RESTRICTIONS,
    confirm_age,
    is_rx,
    requires_confirmation,
    restriction_label,
)


class TestRestrictionLabel:
    @pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
    def test_known_levels(self, level: int) -> None:
        assert restriction_label(level) == AGE_RESTRICTIONS[level]

    def test_18_plus_label(self) -> None:
        assert restriction_label(4) == "18+"

    def test_rx_label(self) -> None:
        assert restriction_label(5) == "18+ (RX)"

    def test_missing_restriction_means_unrestricted(self) -> None:
        assert restriction_label(None) == "Без ограничений"

    def test_unknown_level_does_not_block(self) -> None:
        assert restriction_label(99) == "Без ограничений"

    def test_real_fixture_has_18_plus(self) -> None:
        import json
        from pathlib import Path

        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "book.json").read_text(encoding="utf-8")
        )
        book = Book(
            slug_url="",
            age_restriction_id=payload["data"]["ageRestriction"]["id"],
        )

        assert requires_confirmation(book)
        assert not is_rx(book)


class TestRequiresConfirmation:
    @pytest.mark.parametrize("level", [1, 2, 3, 4])
    def test_confirmable_levels(self, level: int) -> None:
        book = Book(slug_url="", age_restriction_id=level)

        assert requires_confirmation(book) is (level > 1)

    def test_unrestricted_books_need_nothing(self) -> None:
        assert requires_confirmation(Book(slug_url="", age_restriction_id=1)) is False
        assert requires_confirmation(Book(slug_url="")) is False

    def test_rx_is_not_auto_confirmed(self) -> None:
        assert requires_confirmation(Book(slug_url="", age_restriction_id=5)) is False
        assert is_rx(Book(slug_url="", age_restriction_id=5)) is True


class TestConfirmAge:
    def test_message_names_label_and_id(self) -> None:
        message = confirm_age(Book(slug_url="", age_restriction_id=4))

        assert "18+" in message
        assert "4" in message

    def test_report_records_confirmation(self) -> None:
        report = Report()

        confirm_age(Book(slug_url="", age_restriction_id=4), report)

        assert report.age_confirmed is True
        assert report.age_restriction_id == 4
        assert report.age_restriction_label == "18+"
        assert report.age_confirmed_chapters == 1
        assert any("18+" in note for note in report.notes)

    def test_confirmation_works_without_report(self) -> None:
        assert confirm_age(Book(slug_url="", age_restriction_id=4), None)

    def test_taking_one_tap_enough_for_whole_book(self) -> None:
        """Подтверждение одно на книгу, а не на каждую главу: блокировки на сервере нет."""
        report = Report()
        book = Book(slug_url="", age_restriction_id=4)

        confirm_age(book, report)

        assert report.age_confirmed_chapters == 1