"""Тесты иерархической нумерации глав."""

from __future__ import annotations

from ranobelib_epub.models import Chapter
from ranobelib_epub.numbering import apply_numbering, assign_labels, chapter_label


def chapter(chapter_id: int, volume: int | str, number: str, sec: int | None = None) -> Chapter:
    return Chapter(
        id=chapter_id,
        volume=volume,  # type: ignore[arg-type]
        number=number,
        name=f"Глава {chapter_id}",
        number_secondary=sec,
    )


class TestChapterLabel:
    def test_volume_and_chapter(self) -> None:
        assert chapter_label(1, 25) == "1.25"

    def test_other_volume(self) -> None:
        assert chapter_label(15, 12) == "15.12"

    def test_fraction_becomes_third_level(self) -> None:
        assert chapter_label(1, "22.5") == "1.22.5"

    def test_number_is_used_verbatim(self) -> None:
        assert chapter_label(2, "7.10") == "2.7.10"

    def test_empty_number_falls_back_to_volume(self) -> None:
        assert chapter_label(3, "") == "3"


class TestAssignLabels:
    def test_number_secondary_is_excluded(self) -> None:
        """Том 46: `number_secondary` повторяет том и в номер не входит."""
        chapters = [chapter(1, 46, "37", sec=46)]

        assign_labels(chapters)

        assert chapters[0].label == "46.37"
        assert chapters[0].label != "46.37.46"

    def test_side_story_number(self) -> None:
        chapters = [chapter(1, 1, "22.5")]

        assign_labels(chapters)

        assert chapters[0].label == "1.22.5"

    def test_unique_numbers_get_no_suffix(self) -> None:
        chapters = [chapter(1, 1, "25"), chapter(2, 15, "12"), chapter(3, 1, "22.5")]

        labels = assign_labels(chapters)

        assert labels == {1: "1.25", 2: "15.12", 3: "1.22.5"}

    def test_collision_gets_ordinal_suffix(self) -> None:
        chapters = [chapter(1, 1, "25"), chapter(2, 1, "25"), chapter(3, 1, "25")]

        labels = assign_labels(chapters)

        assert labels == {1: "1.25", 2: "1.25-2", 3: "1.25-3"}

    def test_no_collisions_on_real_book(self) -> None:
        """Все 735 пар `(volume, number)` книги 94231 уникальны — метки без суффиксов."""
        chapters = [chapter(i, i % 46 + 1, str(i)) for i in range(735)]

        labels = assign_labels(chapters)

        assert len(set(labels.values())) == 735
        assert all("-" not in value for value in labels.values())


class TestSorting:
    def test_volume_precedes_number(self) -> None:
        chapters = [chapter(1, 2, "1"), chapter(2, 1, "25")]

        ordered = apply_numbering(chapters)

        assert [c.id for c in ordered] == [2, 1]

    def test_fraction_sorts_as_child(self) -> None:
        chapters = [
            chapter(1, 1, "5"),
            chapter(2, 1, "6"),
            chapter(3, 1, "6.5"),
            chapter(4, 1, "7"),
        ]

        ordered = apply_numbering(chapters)

        assert [c.label for c in ordered] == ["1.5", "1.6", "1.6.5", "1.7"]

    def test_side_story_follows_its_chapter(self) -> None:
        chapters = [chapter(1, 1, "22.6"), chapter(2, 1, "22"), chapter(3, 1, "23")]

        ordered = apply_numbering(chapters)

        assert [c.label for c in ordered] == ["1.22", "1.22.6", "1.23"]

    def test_string_numbers_are_sorted_numerically(self) -> None:
        """Сайт отдаёт `number` строкой: `10` должен идти раньше `9`."""
        chapters = [chapter(1, 1, "9"), chapter(2, 1, "10")]

        ordered = apply_numbering(chapters)

        assert [c.label for c in ordered] == ["1.9", "1.10"]

    def test_equal_keys_keep_input_order(self) -> None:
        chapters = [chapter(7, 1, "25", sec=2), chapter(8, 1, "25")]

        ordered = apply_numbering(chapters)

        assert [c.id for c in ordered] == [7, 8]
        assert [c.label for c in ordered] == ["1.25", "1.25-2"]

    def test_no_chapter_is_lost(self) -> None:
        chapters = [chapter(i, 1, str(i)) for i in range(1, 101)]

        ordered = apply_numbering(chapters)

        assert len(ordered) == 100
        assert {c.id for c in ordered} == set(range(1, 101))

    def test_sort_is_stable_across_repeated_calls(self) -> None:
        chapters = [chapter(1, 2, "1"), chapter(2, 1, "3"), chapter(3, 2, "0")]

        first = [c.id for c in apply_numbering(list(chapters))]
        second = [c.id for c in apply_numbering(list(chapters))]

        assert first == second == [2, 3, 1]
