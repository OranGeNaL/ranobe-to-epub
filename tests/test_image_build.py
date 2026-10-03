"""Сквозные тесты встраивания иллюстраций (задачи 8.5, 8.6, 14.6, 15.5)."""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

from PIL import Image

from ranobelib_epub.images.pipeline import collect_image_keys
from ranobelib_epub.models import Attachment, Book, Chapter, ChapterContent
from ranobelib_epub.pipeline.downloader import ChapterDownloader, ChapterTask
from ranobelib_epub.pipeline.report import ReportRecorder

FIXTURES = Path(__file__).parent / "fixtures"


def png_bytes(width: int = 40, height: int = 30, color=(200, 10, 10, 128)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def doc_with_images(*keys: str) -> dict:
    content = []
    if keys:
        content.append({"type": "image", "attrs": {"images": [{"image": key} for key in keys]}})
    content.append({"type": "paragraph", "content": [{"type": "text", "text": "Текст"}]})
    return {"type": "doc", "content": content}


class FakeClient:
    def __init__(self, payloads: dict[str, bytes] | None = None) -> None:
        self.payloads = payloads or {}
        self.requested: list[str] = []
        self.headers: list[dict] = []

    async def get_bytes(self, url: str, headers: dict | None = None) -> bytes:
        self.requested.append(url)
        self.headers.append(dict(headers or {}))
        if url not in self.payloads:
            raise RuntimeError("404: изображение недоступно")
        return self.payloads[url]


class FakeSource:
    def __init__(self, contents: dict[int, ChapterContent], client: FakeClient) -> None:
        self.contents = contents
        self.client = client

    async def fetch_chapter_content(self, slug, chapter, branch_id):
        return self.contents[chapter.id]


def chapter(index: int, label: str | None = None) -> Chapter:
    return Chapter(
        id=index,
        volume=1,
        number=str(index),
        name="Глава",
        branch_id=18996,
        label=label or f"1.{index}",
    )


def make_content(keys: tuple[str, ...], attachments: tuple[Attachment, ...]) -> ChapterContent:
    return ChapterContent(doc=doc_with_images(*keys), attachments=attachments, branch_id=18996)


class TestCollectImageKeys:
    def test_nested_and_deduplicated(self) -> None:
        document = {
            "type": "doc",
            "content": [
                {
                    "type": "image",
                    "attrs": {"images": [{"image": "a"}, {"image": "b"}]},
                },
                {
                    "type": "blockquote",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "image", "attrs": {"images": [{"image": "a"}]}}
                            ],
                        }
                    ],
                },
            ],
        }

        assert collect_image_keys(document) == ["a", "b"]

    def test_document_without_images(self) -> None:
        assert collect_image_keys({"type": "doc", "content": []}) == []


class TestImageDownload:
    def test_image_is_embedded_with_relative_href(self) -> None:
        url = "https://ranobelib.me/uploads/ranobe/1/a.png"
        client = FakeClient({url: png_bytes()})
        source = FakeSource(
            {1: make_content(("a",), (Attachment(name="a", url="/uploads/ranobe/1/a.png"),))},
            client,
        )
        recorder = ReportRecorder(total_chapters=1)
        downloader = ChapterDownloader(source, recorder, max_image_mb=1e-9)

        results = asyncio.run(downloader.fetch_all([ChapterTask(chapter(1))], book_slug="slug"))

        item = results[0]
        assert item.fragment.count("<img") == 1
        assert 'src="../Images/img_0001.jpg"' in item.fragment
        assert item.conversion.warnings == []
        assert len(item.assets) == 1
        assert item.assets[0].filename == "Images/img_0001.jpg"
        assert recorder.report.missing_images == []
        assert client.headers[0]["Referer"] == "https://ranobelib.me/"

    def test_numbers_are_global_across_chapters(self) -> None:
        url_a = "https://ranobelib.me/uploads/ranobe/1/a.png"
        url_b = "https://ranobelib.me/uploads/ranobe/1/b.png"
        client = FakeClient({url_a: png_bytes(), url_b: png_bytes()})
        source = FakeSource(
            {
                1: make_content(("a",), (Attachment(name="a", url="/uploads/ranobe/1/a.png"),)),
                2: make_content(("b",), (Attachment(name="b", url="/uploads/ranobe/1/b.png"),)),
            },
            client,
        )
        recorder = ReportRecorder(total_chapters=2)
        downloader = ChapterDownloader(source, recorder, max_image_mb=1e-9)

        results = asyncio.run(
            downloader.fetch_all(
                [ChapterTask(chapter(1)), ChapterTask(chapter(2))], book_slug="slug"
            )
        )

        names = [asset.filename for item in results for asset in item.assets]
        assert names == ["Images/img_0001.jpg", "Images/img_0002.jpg"]

    def test_missing_attachment_leaves_placeholder(self) -> None:
        client = FakeClient()
        source = FakeSource({1: make_content(("ghost",), ())}, client)
        recorder = ReportRecorder(total_chapters=1)
        downloader = ChapterDownloader(source, recorder)

        results = asyncio.run(downloader.fetch_all([ChapterTask(chapter(1))], book_slug="slug"))

        assert "изображение недоступно" in results[0].fragment
        assert results[0].assets == []
        assert len(recorder.report.missing_images) == 1
        assert recorder.report.missing_images[0].reference == "ghost"
        assert recorder.report.missing_images[0].chapter_label == "1.1"

    def test_fetch_failure_leaves_placeholder_and_reason(self) -> None:
        client = FakeClient()
        source = FakeSource(
            {1: make_content(("a",), (Attachment(name="a", url="/uploads/ranobe/1/a.png"),))},
            client,
        )
        recorder = ReportRecorder(total_chapters=1)
        downloader = ChapterDownloader(source, recorder)

        results = asyncio.run(downloader.fetch_all([ChapterTask(chapter(1))], book_slug="slug"))

        assert "изображение недоступно" in results[0].fragment
        assert len(recorder.report.missing_images) == 1
        assert "404" in recorder.report.missing_images[0].reason

    def test_images_disabled_skips_network(self) -> None:
        client = FakeClient()
        source = FakeSource(
            {1: make_content(("a",), (Attachment(name="a", url="/uploads/ranobe/1/a.png"),))},
            client,
        )
        recorder = ReportRecorder(total_chapters=1)
        downloader = ChapterDownloader(source, recorder)
        task = ChapterTask(chapter(1), include_images=False)

        results = asyncio.run(downloader.fetch_all([task], book_slug="slug"))

        assert client.requested == []
        assert 'src="a"' in results[0].fragment

    def test_image_progress_is_reported(self) -> None:
        url = "https://ranobelib.me/uploads/ranobe/1/a.png"
        client = FakeClient({url: png_bytes()})
        source = FakeSource(
            {1: make_content(("a",), (Attachment(name="a", url="/uploads/ranobe/1/a.png"),))},
            client,
        )
        recorder = ReportRecorder(total_chapters=1)
        seen = []
        downloader = ChapterDownloader(
            source, recorder, on_progress=lambda p: seen.append(p), max_image_mb=1e-9
        )

        asyncio.run(downloader.fetch_all([ChapterTask(chapter(1))], book_slug="slug"))

        assert seen[-1].images_total == 1
        assert seen[-1].images_done == 1


class TestCover:
    def test_cover_downloaded_and_named(self) -> None:
        url = "https://ranobelib.me/uploads/covers/big.png"
        client = FakeClient({url: png_bytes(120, 160)})
        source = FakeSource({}, client)
        recorder = ReportRecorder()
        downloader = ChapterDownloader(source, recorder, max_image_mb=1e-9)
        book = Book(slug_url="94231--x", cover="/uploads/covers/big.png")

        cover = asyncio.run(downloader.fetch_cover(book))

        assert cover is not None
        assert cover.filename == "Images/img_0001.jpg"
        assert cover.data[:2] == b"\xff\xd8"

    def test_missing_cover_is_recorded_not_fatal(self) -> None:
        client = FakeClient()
        source = FakeSource({}, client)
        recorder = ReportRecorder()
        downloader = ChapterDownloader(source, recorder)
        book = Book(slug_url="94231--x", cover="/uploads/covers/absent.png")

        cover = asyncio.run(downloader.fetch_cover(book))

        assert cover is None
        assert len(recorder.report.missing_images) == 1

    def test_no_cover_field_is_ignored(self) -> None:
        client = FakeClient()
        source = FakeSource({}, client)
        downloader = ChapterDownloader(source, ReportRecorder())

        assert asyncio.run(downloader.fetch_cover(Book(slug_url="94231--x"))) is None
        assert client.requested == []


class TestEpubWithImages:
    def test_archive_contains_images_and_manifest_declares_them(self, tmp_path: Path) -> None:
        from ranobelib_epub.cli.main import write_epub
        from ranobelib_epub.cli.options import Options

        url = "https://ranobelib.me/uploads/ranobe/1/a.png"
        cover_url = "https://ranobelib.me/uploads/covers/c.png"
        client = FakeClient({url: png_bytes(), cover_url: png_bytes(120, 160)})
        source = FakeSource(
            {1: make_content(("a",), (Attachment(name="a", url="/uploads/ranobe/1/a.png"),))},
            client,
        )
        recorder = ReportRecorder(total_chapters=1)
        downloader = ChapterDownloader(source, recorder, max_image_mb=1e-9)
        fetched = asyncio.run(
            downloader.fetch_all([ChapterTask(chapter(1))], book_slug="slug")
        )
        book = Book(
            slug_url="94231--x", rus_name="Книга", cover="/uploads/covers/c.png"
        )
        cover = asyncio.run(downloader.fetch_cover(book))

        target = tmp_path / "book.epub"
        write_epub(target, book, fetched, Options(), recorder, cover=cover)

        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            opf = archive.read("EPUB/package.opf").decode("utf-8")

        assert "EPUB/Images/img_0001.jpg" in names
        assert "EPUB/Images/img_0002.jpg" in names
        assert "Images/img_0001.jpg" in opf
        assert 'properties="cover-image"' in opf
