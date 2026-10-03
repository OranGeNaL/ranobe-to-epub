"""Тесты сборки EPUB 3 (задачи 10.1-10.8)."""

from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ranobelib_epub.epub.builder import (
    MIMETYPE,
    MIMETYPE_VALUE,
    OPF_DIR,
    TEXT_DIR,
    ChapterDocument,
    EpubBuilder,
    expected_parts,
    iter_chapter_files,
)
from ranobelib_epub.epub.naming import (
    assign_chapter_filenames,
    chapter_filename,
    is_safe,
    sanitize,
)
from ranobelib_epub.epub.parts import (
    ChapterEntry,
    book_identifier,
    chapter_document,
    container_xml,
    nav_xhtml,
    package_opf,
    toc_ncx,
)
from ranobelib_epub.models import Book, Chapter
from ranobelib_epub.source.numbering import assign_labels, sort_chapters
from ranobelib_epub.source.parsing import parse_chapters

FIXTURES = Path(__file__).parent / "fixtures"
OPF_TEXT_DIR = "TEXT"
JPEG_BYTES = bytes.fromhex("ffd8ffd9")
OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
NCX_NS = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}

BOOK = Book(
    slug_url="https://ranobelib.me/book/94231--test",
    book_id=94231,
    name="Original",
    rus_name="Русское название",
    author="Автор книги",
    summary="Описание книги",
    in_language="ru-RU",
)


def build_one_chapter(tmp_path: Path, **kwargs) -> tuple[EpubBuilder, Path]:
    builder = EpubBuilder(BOOK, build_date=date(2026, 1, 1))
    builder.add_chapter(
        ChapterDocument(
            filename=f"{OPF_TEXT_DIR}/1.1_Пролог.html",
            title="1.1 Пролог",
            xhtml=chapter_document("1.1 Пролог", "<p>Привет</p>"),
        )
    )
    builder.add_image("Images/img_0001.jpg", _real_jpeg(), "image/jpeg")
    return builder, tmp_path / "book.epub"


class TestMimetype:
    def test_mimetype_is_first_and_stored(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)
        builder.write(target)

        with zipfile.ZipFile(target) as archive:
            first = archive.infolist()[0]

        assert first.filename == MIMETYPE
        assert first.compress_type == zipfile.ZIP_STORED

    def test_mimetype_content(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)
        builder.write(target)

        with zipfile.ZipFile(target) as archive:
            assert archive.read(MIMETYPE) == MIMETYPE_VALUE.encode()

    def test_other_entries_are_compressed(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)
        builder.write(target)

        with zipfile.ZipFile(target) as archive:
            compressed = [i.compress_type for i in archive.infolist()[1:]]

        assert compressed and all(value == zipfile.ZIP_DEFLATED for value in compressed)


class TestContainer:
    def test_container_parses_and_points_to_package(self) -> None:
        root = ET.fromstring(container_xml())
        rootfile = root[0][0]

        assert rootfile.get("full-path") == "EPUB/package.opf"
        assert rootfile.get("media-type") == "application/oebps-package+xml"

    def test_container_is_present_in_archive(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)
        builder.write(target)

        with zipfile.ZipFile(target) as archive:
            assert "META-INF/container.xml" in archive.namelist()


class TestPackageMetadata:
    def test_title_falls_back_to_original_name(self) -> None:
        book = Book(slug_url="u", book_id=1, name="Original", rus_name="Русское")

        root = ET.fromstring(package_opf(book, []))

        assert root.find("opf:metadata/dc:title", OPF_NS).text == "Русское"

    def test_title_without_russian_name(self) -> None:
        book = Book(slug_url="u", book_id=1, name="Original")

        root = ET.fromstring(package_opf(book, []))

        assert root.find("opf:metadata/dc:title", OPF_NS).text == "Original"

    def test_language_is_taken_from_book(self) -> None:
        root = ET.fromstring(package_opf(BOOK, []))

        assert root.find("opf:metadata/dc:language", OPF_NS).text == "ru-RU"

    def test_language_defaults_to_russian(self) -> None:
        root = ET.fromstring(package_opf(Book(slug_url="u", book_id=1), []))

        assert root.find("opf:metadata/dc:language", OPF_NS).text == "ru"

    def test_creator_and_description_present(self) -> None:
        root = ET.fromstring(package_opf(BOOK, []))

        assert root.find("opf:metadata/dc:creator", OPF_NS).text == "Автор книги"
        assert root.find("opf:metadata/dc:description", OPF_NS).text == "Описание книги"

    def test_creator_omitted_when_unknown(self) -> None:
        root = ET.fromstring(package_opf(Book(slug_url="u", book_id=1), []))

        assert root.find("opf:metadata/dc:creator", OPF_NS) is None

    def test_identifier_is_stable_between_builds(self, tmp_path: Path) -> None:
        first = ET.fromstring(package_opf(BOOK, [build_entry()]))
        second = ET.fromstring(package_opf(BOOK, [build_entry()]))

        assert first.find("opf:metadata/dc:identifier", OPF_NS).text == (
            second.find("opf:metadata/dc:identifier", OPF_NS).text
        )

    def test_identifier_differs_for_another_book(self) -> None:
        other = Book(slug_url="https://ranobelib.me/book/1--x", book_id=1)

        assert book_identifier(BOOK) != book_identifier(other)

    def test_identifier_is_a_uuid_urn(self) -> None:
        assert book_identifier(BOOK).startswith("urn:uuid:")

    def test_date_is_included(self) -> None:
        root = ET.fromstring(package_opf(BOOK, [], build_date=date(2026, 1, 1)))

        assert root.find("opf:metadata/dc:date", OPF_NS).text == "2026-01-01"

    def test_subjects_from_genres(self) -> None:
        book = Book(slug_url="u", book_id=1, genres=("Боевик", "Фэнтези"))

        root = ET.fromstring(package_opf(book, []))

        assert "Фэнтези" in root.find("opf:metadata/dc:subject", OPF_NS).text

    def test_manifest_and_spine_cover_all_chapters(self) -> None:
        entries = [ChapterEntry(f"{i} Глава", f"TEXT/{i}.html", i) for i in range(1, 4)]

        root = ET.fromstring(package_opf(BOOK, entries))

        items = root.findall("opf:manifest/opf:item", OPF_NS)
        refs = root.findall("opf:spine/opf:itemref", OPF_NS)
        chapter_items = [i for i in items if i.get("href", "").startswith("TEXT/")]

        assert len(chapter_items) == 3
        assert len(refs) == 3
        assert root.find("opf:spine", OPF_NS).get("toc") == "ncx"

    def test_nav_item_declares_property(self) -> None:
        root = ET.fromstring(package_opf(BOOK, []))

        nav = [i for i in root.findall("opf:manifest/opf:item", OPF_NS) if i.get("id") == "nav"]

        assert nav[0].get("properties") == "nav"


class TestCover:
    def test_cover_is_declared_with_meta_and_property(self) -> None:
        root = ET.fromstring(
            package_opf(
                BOOK,
                [],
                images=[("Images/cover.jpg", "image/jpeg")],
                cover_item="Images/cover.jpg",
            )
        )

        assert (
            root.find('opf:metadata/opf:meta[@name="cover"]', OPF_NS).get("content")
            == "cover-image"
        )
        cover = [
            item
            for item in root.findall("opf:manifest/opf:item", OPF_NS)
            if item.get("id") == "cover-image"
        ]
        assert cover[0].get("properties") == "cover-image"
        assert cover[0].get("href") == "Images/cover.jpg"

    def test_every_embedded_image_is_declared(self) -> None:
        """Без объявления в манифесте epubcheck отвергает книгу (RSC-008)."""
        images = [("Images/img_0001.jpg", "image/jpeg"), ("Images/img_0002.jpg", "image/jpeg")]

        root = ET.fromstring(package_opf(BOOK, [build_entry()], images=images))

        hrefs = [item.get("href") for item in root.findall("opf:manifest/opf:item", OPF_NS)]
        for href, _ in images:
            assert href in hrefs

    def test_chapter_images_use_relative_href(self) -> None:
        """Ссылка из главы в `TEXT/` на картинку в `Images/` — относительная."""
        from ranobelib_epub.images.pipeline import chapter_image_href, epub_filename

        name = epub_filename(1, "image/jpeg")

        assert chapter_image_href(name) == "../Images/img_0001.jpg"
        assert epub_filename(1, "image/jpeg") == name

    def test_book_without_cover_builds(self, tmp_path: Path) -> None:
        builder = EpubBuilder(BOOK, build_date=date(2026, 1, 1))
        builder.add_chapter(
            ChapterDocument(f"{TEXT_DIR}/1.1_A.html", "1.1", chapter_document("1.1", "<p>a</p>"))
        )

        result = builder.write(tmp_path / "nocover.epub")

        assert result.chapters == 1
        with zipfile.ZipFile(result.path) as archive:
            opf = ET.fromstring(archive.read("EPUB/package.opf"))
        assert opf.find('opf:metadata/opf:meta[@name="cover"]', OPF_NS) is None

    def test_cover_image_is_embedded(self, tmp_path: Path) -> None:
        builder = EpubBuilder(BOOK, build_date=date(2026, 1, 1))
        builder.set_cover("Images/cover.jpg")
        builder.add_image("Images/cover.jpg", _real_jpeg(), "image/jpeg")

        result = builder.write(tmp_path / "cover.epub")

        with zipfile.ZipFile(result.path) as archive:
            assert f"{OPF_DIR}/Images/cover.jpg" in archive.namelist()


class TestNavigation:
    def test_nav_lists_all_chapters_in_order(self) -> None:
        entries = [
            ChapterEntry("1.25 Первая", "TEXT/1.25_Первая.html", 1),
            ChapterEntry("15.12 Вторая", "TEXT/15.12_Вторая.html", 2),
            ChapterEntry("1.22.5 Третья", "TEXT/1.22.5_Третья.html", 3),
        ]

        root = ET.fromstring(nav_xhtml(BOOK, entries))
        nav = root.find(".//{http://www.w3.org/1999/xhtml}nav")
        items = nav.findall(".//{http://www.w3.org/1999/xhtml}li")

        assert [item[0].text for item in items] == ["1.25 Первая", "15.12 Вторая", "1.22.5 Третья"]
        assert [item[0].get("href") for item in items] == [e.href for e in entries]

    def test_nav_has_toc_type(self) -> None:
        root = ET.fromstring(nav_xhtml(BOOK, []))

        assert (
            root.find(".//{http://www.w3.org/1999/xhtml}nav").get(
                "{http://www.idpf.org/2007/ops}type"
            )
            == "toc"
        )

    def test_ncx_play_order_and_numbers(self) -> None:
        entries = [
            ChapterEntry("1.25 Первая", "TEXT/a.html", 1),
            ChapterEntry("15.12 Вторая", "TEXT/b.html", 2),
        ]

        root = ET.fromstring(toc_ncx(entries, BOOK))
        points = root.findall("ncx:navMap/ncx:navPoint", NCX_NS)

        assert [p.get("playOrder") for p in points] == ["1", "2"]
        labels = [p.find("ncx:navLabel/ncx:text", NCX_NS).text for p in points]
        srcs = [p.find("ncx:content", NCX_NS).get("src") for p in points]
        assert labels == ["1.25 Первая", "15.12 Вторая"]
        assert srcs == ["TEXT/a.html", "TEXT/b.html"]

    def test_ncx_and_nav_agree(self) -> None:
        entries = [ChapterEntry(f"{i}.1 Глава", f"TEXT/{i}.html", i) for i in range(1, 6)]

        nav = ET.fromstring(nav_xhtml(BOOK, entries))
        ncx = ET.fromstring(toc_ncx(entries, BOOK))

        nav_hrefs = [
            item[0].get("href")
            for item in nav.findall(
                ".//{http://www.w3.org/1999/xhtml}ol/{http://www.w3.org/1999/xhtml}li"
            )
        ]
        ncx_srcs = [
            point.find("ncx:content", NCX_NS).get("src")
            for point in ncx.findall("ncx:navMap/ncx:navPoint", NCX_NS)
        ]

        assert nav_hrefs == ncx_srcs


class TestRealBookNavigation:
    """Проверка на всех 735 главах реальной книги (10.5)."""

    @pytest.fixture(scope="class")
    @staticmethod
    def chapters() -> list[Chapter]:
        payload = json.loads((FIXTURES / "chapters.json").read_text(encoding="utf-8"))
        chapters = parse_chapters(payload)
        assign_labels(chapters)
        return sort_chapters(chapters)

    def test_nav_has_735_items(self, chapters: list[Chapter]) -> None:
        entries = [
            ChapterEntry(ch.label or "", f"TEXT/{i}.html", i)
            for i, ch in enumerate(chapters, start=1)
        ]

        root = ET.fromstring(nav_xhtml(BOOK, entries))
        items = root.findall(".//{http://www.w3.org/1999/xhtml}ol/{http://www.w3.org/1999/xhtml}li")

        assert len(items) == 735

    def test_numbers_match_model_and_order(self, chapters: list[Chapter]) -> None:
        entries = [
            ChapterEntry(ch.label or "", f"TEXT/{i}.html", i)
            for i, ch in enumerate(chapters, start=1)
        ]

        root = ET.fromstring(nav_xhtml(BOOK, entries))
        items = root.findall(".//{http://www.w3.org/1999/xhtml}ol/{http://www.w3.org/1999/xhtml}li")

        assert [item[0].text for item in items] == [ch.label for ch in chapters]

    def test_order_is_ascending(self, chapters: list[Chapter]) -> None:
        keys = [chapter.sort_key for chapter in chapters]

        assert keys == sorted(keys)

    def test_fractional_and_nested_numbers_present(self, chapters: list[Chapter]) -> None:
        labels = [chapter.label for chapter in chapters]

        assert "1.22.5" in labels
        assert any(label.count(".") == 2 for label in labels)


class TestFileNames:
    def test_prefix_is_hierarchical_number(self) -> None:
        assert chapter_filename("1.25", "Пролог", 1, set()) == "TEXT/1.25_Пролог.html"

    def test_nested_number_kept(self) -> None:
        name = chapter_filename("1.22.5", "Бонус", 1, set())

        assert name == "TEXT/1.22.5_Бонус.html"

    @pytest.mark.parametrize(
        ("raw", "forbidden"),
        [
            ("Глава/одна", "/"),
            ("Глава:две", ":"),
            ("Глава*три", "*"),
            ('Глава"четыре', '"'),
            ("Глава?пять", "?"),
            ("Глава<шесть>", "<"),
            ("Глава\\семь", "\\"),
        ],
    )
    def test_forbidden_characters_are_replaced(self, raw: str, forbidden: str) -> None:
        name = chapter_filename("1.1", raw, 1, set())
        stem = name.rsplit("/", 1)[-1].removesuffix(".html")

        assert forbidden not in stem
        assert is_safe(stem)

    def test_readability_is_preserved(self) -> None:
        name = chapter_filename("1.1", 'Глава "Первый/день"', 1, set())

        assert "Глава" in name
        assert "Первый" in name
        assert "день" in name

    def test_same_titles_with_different_numbers_are_distinct(self) -> None:
        taken: set[str] = set()
        names = [
            chapter_filename(label, "Пролог", index, taken)
            for index, label in enumerate(["1.1", "1.2", "1.3"], start=1)
        ]

        assert len(set(names)) == 3
        assert names == [
            "TEXT/1.1_Пролог.html",
            "TEXT/1.2_Пролог.html",
            "TEXT/1.3_Пролог.html",
        ]

    def test_identical_label_and_title_get_counter(self) -> None:
        """Так бывает после разрешения коллизий: одинаковый номер — разные главы."""
        taken: set[str] = set()

        first = chapter_filename("1.25", "Пролог", 1, taken)
        second = chapter_filename("1.25", "Пролог", 2, taken)

        assert first == "TEXT/1.25_Пролог.html"
        assert second == "TEXT/1.25_Пролог_2.html"

    def test_spaces_in_title_do_not_reach_references(self) -> None:
        """Пробел в `href` — недопустимый URL; имя файла должно быть без пробелов."""
        name = chapter_filename("1.2", "Как сыграть свою козырную карту", 1, set())

        assert " " not in name
        assert name == "TEXT/1.2_Как_сыграть_свою_козырную_карту.html"

    def test_colliding_numbers_stay_distinct(self) -> None:
        taken: set[str] = set()
        first = chapter_filename("1.25", "Пролог", 1, taken)
        second = chapter_filename("1.25-2", "Пролог", 2, taken)

        assert first != second
        assert first == "TEXT/1.25_Пролог.html"
        assert second == "TEXT/1.25-2_Пролог.html"

    def test_long_chinese_title_is_truncated(self) -> None:
        title = "这是一个非常长的章节标题" * 10

        name = chapter_filename("1.1", title, 1, set())

        assert len(name.split("/")[-1]) < 120
        assert name.endswith(".html")

    def test_empty_title_gets_fallback(self) -> None:
        assert sanitize("") == "Без названия"
        assert "Без_названия" in chapter_filename("1.1", "   ", 1, set())

    def test_control_characters_are_stripped(self) -> None:
        assert sanitize("Глава\x00\x07") == "Глава"

    def test_trailing_dots_are_trimmed(self) -> None:
        assert sanitize("Глава...") == "Глава"

    def test_all_names_unique_for_real_book(self) -> None:
        payload = json.loads((FIXTURES / "chapters.json").read_text(encoding="utf-8"))
        chapters = parse_chapters(payload)
        assign_labels(chapters)

        names = assign_chapter_filenames(sort_chapters(chapters))

        assert len(set(names)) == len(names) == 735


class TestMinimalBuild:
    def test_minimal_epub_has_all_required_parts(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)

        result = builder.write(target)

        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
        for part in expected_parts():
            assert part in names, part
        assert result.chapters == 1
        assert result.images == 1

    def test_all_xml_parts_are_well_formed(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)
        builder.write(target)

        with zipfile.ZipFile(target) as archive:
            for name in archive.namelist():
                if name.endswith((".xml", ".opf", ".xhtml", ".ncx")):
                    ET.fromstring(archive.read(name))

    def test_chapter_file_is_inside_archive(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)
        builder.write(target)

        with zipfile.ZipFile(target) as archive:
            assert f"{OPF_DIR}/{OPF_TEXT_DIR}/1.1_Пролог.html" in archive.namelist()

    def test_chapter_xhtml_is_embedded(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)
        builder.write(target)

        with zipfile.ZipFile(target) as archive:
            body = archive.read(f"{OPF_DIR}/{OPF_TEXT_DIR}/1.1_Пролог.html").decode()

        assert "<p>Привет</p>" in body

    def test_manifest_hrefs_resolve_inside_package(self, tmp_path: Path) -> None:
        """`href` в OPF разрешаются относительно каталога `EPUB/`."""
        builder = EpubBuilder(BOOK, build_date=date(2026, 1, 1))
        builder.add_chapter(
            ChapterDocument(
                f"{OPF_TEXT_DIR}/1.1_A.html", "1.1 A", chapter_document("1.1 A", "<p>a</p>")
            )
        )
        builder.add_image("Images/img_0001.jpg", JPEG_BYTES, "image/jpeg")

        result = builder.write(tmp_path / "hrefs.epub")

        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())
            opf = ET.fromstring(archive.read("EPUB/package.opf"))

        hrefs = [item.get("href") for item in opf.findall("opf:manifest/opf:item", OPF_NS)]
        chapter_hrefs = [href for href in hrefs if href.startswith(OPF_TEXT_DIR)]

        assert chapter_hrefs, "в манифесте нет ссылок на главы"
        for href in hrefs:
            assert f"{OPF_DIR}/{href}" in names, f"{href} не разрешается в архиве"

    def test_two_builds_produce_same_layout(self, tmp_path: Path) -> None:
        first = EpubBuilder(BOOK, build_date=date(2026, 1, 1))
        second = EpubBuilder(BOOK, build_date=date(2026, 1, 1))
        for builder in (first, second):
            builder.add_chapter(
                ChapterDocument(
                    f"{TEXT_DIR}/1.1_A.html", "1.1 A", chapter_document("1.1 A", "<p>a</p>")
                )
            )

        result_a = first.write(tmp_path / "a.epub")
        result_b = second.write(tmp_path / "b.epub")

        assert result_a.files == result_b.files
        with zipfile.ZipFile(result_a.path) as left, zipfile.ZipFile(result_b.path) as right:
            for name in result_a.files:
                assert left.read(name) == right.read(name), name

    def test_size_is_reported(self, tmp_path: Path) -> None:
        builder, target = build_one_chapter(tmp_path)

        result = builder.write(target)

        assert result.size == target.stat().st_size > 0

    def test_chapter_files_listed_in_order(self, tmp_path: Path) -> None:
        builder = EpubBuilder(BOOK, build_date=date(2026, 1, 1))
        for index in range(1, 4):
            builder.add_chapter(
                ChapterDocument(
                    f"{OPF_TEXT_DIR}/{index}.html",
                    str(index),
                    chapter_document(str(index), "<p>x</p>"),
                )
            )

        result = builder.write(tmp_path / "many.epub")

        assert list(iter_chapter_files(result)) == [f"{TEXT_DIR}/{i}.html" for i in (1, 2, 3)]

    def test_epubcheck_accepts_file(self, tmp_path: Path) -> None:
        """Сторонний валидатор: EPUB должен пройти без ошибок и предупреждений.

        Путь к валидатору задаётся `EPUBCHECK_JAR` (jar-файл) или `EPUBCHECK`
        (исполняемый файл); без них проверка пропускается, а обязательные части
        всё равно разбираются тестами выше.
        """
        command = epubcheck_command()
        if command is None:
            pytest.skip("epubcheck недоступен: задайте EPUBCHECK_JAR или EPUBCHECK")

        builder, target = build_one_chapter(tmp_path)
        builder.add_chapter(
            ChapterDocument(
                f"{OPF_TEXT_DIR}/1.2_Иллюстрация.html",
                "1.2 Иллюстрация",
                chapter_document(
                    "1.2 Иллюстрация", '<p><img src="../Images/img_0001.jpg" alt=""/></p>'
                ),
            )
        )
        builder.add_image("Images/img_0001.jpg", _real_jpeg(), "image/jpeg")
        builder.write(target)

        import subprocess

        completed = subprocess.run(
            [*command, str(target)], capture_output=True, text=True, check=False
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr


def _real_jpeg() -> bytes:
    """Настоящий JPEG из реальной фикстуры — заглушки epubcheck не принимает."""
    from ranobelib_epub.images.pipeline import compress_image

    return compress_image((FIXTURES / "image_2210x1582.png").read_bytes()).data


def epubcheck_command() -> list[str] | None:
    """Команда запуска epubcheck из переменных окружения или `PATH`."""
    import os
    import shutil

    jar = os.environ.get("EPUBCHECK_JAR")
    if jar and Path(jar).exists():
        return ["java", "-jar", jar]
    binary = os.environ.get("EPUBCHECK") or shutil.which("epubcheck")
    if binary:
        return [binary]
    return None


def build_entry() -> ChapterEntry:
    return ChapterEntry("1.1 Пролог", "TEXT/1.1_Пролог.html", 1)
