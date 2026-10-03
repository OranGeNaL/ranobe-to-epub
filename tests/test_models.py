from __future__ import annotations

from ranobelib_epub.models import Book, Chapter, Report, TranslationBranch, number_sort_key


class TestBookOptionalFields:
    def test_optional_fields_are_none(self) -> None:
        book = Book(slug_url="94231--rezero")

        assert book.slug_url == "94231--rezero"
        assert book.book_id is None
        assert book.name is None
        assert book.rus_name is None
        assert book.author is None
        assert book.cover is None
        assert book.status is None
        assert book.release_date is None
        assert book.year is None
        assert book.genres == ()
        assert book.summary is None
        assert book.in_language is None
        assert book.age_restriction_id is None
        assert book.age_restriction_label is None

    def test_title_prefers_russian_name(self) -> None:
        assert Book(slug_url="a--b", name="Original", rus_name="Русский").title == "Русский"

    def test_title_falls_back_to_original_name(self) -> None:
        assert Book(slug_url="a--b", name="Original").title == "Original"

    def test_title_falls_back_to_slug(self) -> None:
        assert Book(slug_url="a--b").title == "a--b"


class TestChapter:
    def test_default_branch_is_first(self) -> None:
        branches = (TranslationBranch(id=1, name="a"), TranslationBranch(id=2, name="b"))
        chapter = Chapter(id=1, volume=1, number="0", name="n", branches=branches)

        assert chapter.default_branch is branches[0]

    def test_default_branch_is_none_without_branches(self) -> None:
        assert Chapter(id=1, volume=1, number="0", name="n").default_branch is None

    def test_sort_key_is_hierarchical(self) -> None:
        side_story = Chapter(id=1, volume=1, number="22.5", name="n")
        main = Chapter(id=2, volume=1, number="22", name="n")

        assert main.sort_key < side_story.sort_key


class TestNumberSortKey:
    def test_volume_precedes_number(self) -> None:
        assert number_sort_key(1, "25") < number_sort_key(2, "1")

    def test_fraction_belongs_to_parent(self) -> None:
        assert number_sort_key(1, "22") < number_sort_key(1, "22.5")

    def test_deep_fraction(self) -> None:
        assert number_sort_key(1, "22.5") < number_sort_key(1, "23")

    def test_non_numeric_tail_is_stable(self) -> None:
        assert number_sort_key(1, "22a") < number_sort_key(1, "23")


class TestReport:
    def test_defaults_are_empty(self) -> None:
        report = Report()

        assert report.total_chapters == 0
        assert report.built_chapters == 0
        assert report.unavailable == []
        assert report.missing_images == []
        assert report.unknown_nodes == []
        assert report.removed_characters == 0
        assert report.chapters_with_removed_characters == 0
        assert report.age_confirmed is False
        assert report.file_size is None

    def test_age_confirmed_requires_confirmed_chapters(self) -> None:
        assert Report(age_confirmed_chapters=1).age_confirmed is True

    def test_numbers_may_arrive_as_ints(self) -> None:
        assert number_sort_key(1, 25) == number_sort_key(1, "25")
