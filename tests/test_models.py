from __future__ import annotations

from ranobelib_epub.models import (
    LANGUAGE_CODES,
    Book,
    Chapter,
    Cover,
    MetadataOverrides,
    Report,
    TranslationBranch,
    apply_overrides,
    number_sort_key,
    resolve_cover_url,
)


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
        assert book.publisher is None
        assert book.series is None
        assert book.series_index is None

    def test_title_prefers_russian_name(self) -> None:
        assert Book(slug_url="a--b", name="Original", rus_name="Русский").title == "Русский"

    def test_title_falls_back_to_original_name(self) -> None:
        assert Book(slug_url="a--b", name="Original").title == "Original"

    def test_title_falls_back_to_slug(self) -> None:
        assert Book(slug_url="a--b").title == "a--b"


class TestLanguageCodes:
    def test_ten_codes_including_russian(self) -> None:
        assert len(LANGUAGE_CODES) == 10
        assert LANGUAGE_CODES == (
            "ru", "en", "ja", "zh", "ko", "de", "fr", "es", "it", "pt",
        )


class TestCover:
    def test_cover_fields_and_defaults(self) -> None:
        cover = Cover(id=7, order=2, label="Том 3", url="https://cover/x.jpg")

        assert cover.id == 7
        assert cover.order == 2
        assert cover.label == "Том 3"
        assert cover.url == "https://cover/x.jpg"

    def test_cover_without_url(self) -> None:
        assert Cover(id=7, order=2, label="Том 3", url=None).url is None


class TestApplyOverrides:
    def book(self) -> Book:
        return Book(
            slug_url="94231--rezero",
            book_id=94231,
            name="Original",
            rus_name="Русское",
            author="Старый автор",
            summary="Старое описание",
            genres=("Старый жанр",),
            in_language="ru",
        )

    def test_full_override_replaces_fields(self) -> None:
        result = apply_overrides(
            self.book(),
            MetadataOverrides(
                title="Новое название",
                author="Новый автор",
                description="Новое описание",
                language="en",
                genres=("Боевик", "Драма"),
                date="2024-01-01",
                publisher="Издательство",
                series="Серия",
                series_index=3,
            ),
        )

        assert result.title == "Новое название"
        assert result.rus_name == "Новое название"
        assert result.author == "Новый автор"
        assert result.summary == "Новое описание"
        assert result.in_language == "en"
        assert result.genres == ("Боевик", "Драма")
        assert result.publisher == "Издательство"
        assert result.series == "Серия"
        assert result.series_index == 3
        assert result.book_id == self.book().book_id, "идентичность не меняется"

    def test_empty_overrides_keep_site_values(self) -> None:
        assert apply_overrides(self.book(), MetadataOverrides()) == self.book()

    def test_only_none_fields_touch_book(self) -> None:
        result = apply_overrides(
            self.book(),
            MetadataOverrides(title="X", author="", series_index=5),
        )

        assert result.rus_name == "X"
        assert result.author == "Старый автор"
        assert result.series_index == 5

    def test_empty_string_means_no_override(self) -> None:
        result = apply_overrides(
            self.book(),
            MetadataOverrides(title="", author="", description="", language=""),
        )

        assert result == self.book()


class TestResolveCoverUrl:
    def setup_method(self) -> None:
        self.book = Book(slug_url="u", cover="https://site/cover.jpg")
        self.covers = (
            Cover(id=1, order=0, label="Том 1", url="https://cover/1.jpg"),
            Cover(id=2, order=1, label="Том 2", url=None),
        )

    def test_default_uses_book_cover(self) -> None:
        assert resolve_cover_url(self.book, self.covers) == "https://site/cover.jpg"

    def test_selected_cover_wins(self) -> None:
        assert resolve_cover_url(self.book, self.covers, cover_id=1) == "https://cover/1.jpg"

    def test_selected_cover_without_url_is_none(self) -> None:
        assert resolve_cover_url(self.book, self.covers, cover_id=2) is None

    def test_unknown_cover_id_is_none(self) -> None:
        assert resolve_cover_url(self.book, self.covers, cover_id=99) is None

    def test_disabled_cover_is_none(self) -> None:
        assert resolve_cover_url(self.book, self.covers, cover_disabled=True) is None

    def test_disabled_beats_selection(self) -> None:
        assert resolve_cover_url(self.book, self.covers, cover_id=1, cover_disabled=True) is None


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
