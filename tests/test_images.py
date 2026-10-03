"""Тесты конвейера иллюстраций (задачи 8.1-8.6)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from ranobelib_epub.convert.tiptap import convert_document
from ranobelib_epub.images.pipeline import (
    DEFAULT_MAX_WIDTH,
    IMAGE_HEADERS,
    SITE_ORIGIN,
    absolute_url,
    assign_filenames,
    attachment_key,
    compress_image,
    epub_filename,
    filter_and_compress,
    image_keys,
    keep_original,
    mark_missing,
    needs_compression,
    placeholder,
    resolve_attachments,
)

FIXTURES = Path(__file__).parent / "fixtures"
REAL_PNG = FIXTURES / "image_2210x1582.png"


@pytest.fixture(scope="module")
def real_png() -> bytes:
    return REAL_PNG.read_bytes()


@pytest.fixture(scope="module")
def images_fixture() -> dict:
    return json.loads((FIXTURES / "chapter_images.json").read_text(encoding="utf-8"))


class TestJoining:
    def test_image_node_keys_come_from_image_field(self, images_fixture: dict) -> None:
        node = _first_image_node(images_fixture)

        assert image_keys(node) == ("8ef3d556-d13b-4709-90bb-6520828aafb5",)

    def test_attachments_are_indexed_by_name(self, images_fixture: dict) -> None:
        chapter = _chapter(images_fixture)

        index = resolve_attachments(chapter)

        assert "8ef3d556-d13b-4709-90bb-6520828aafb5" in index
        assert index["8ef3d556-d13b-4709-90bb-6520828aafb5"].extension == "png"

    def test_absolute_url_for_spec_example(self, images_fixture: dict) -> None:
        chapter = _chapter(images_fixture)
        attachment = resolve_attachments(chapter)["8ef3d556-d13b-4709-90bb-6520828aafb5"]

        url = absolute_url(attachment)

        assert url == (
            "https://ranobelib.me/uploads/ranobe/94231/chapters/3422985/"
            "8ef3d556-d13b-4709-90bb-6520828aafb5.png"
        )

    def test_absolute_url_keeps_absolute_input(self) -> None:
        assert absolute_url({"url": "https://cdn.example/x.png"}) == "https://cdn.example/x.png"

    def test_attachment_key_accepts_both_field_names(self) -> None:
        assert attachment_key({"image": "uuid"}) == "uuid"
        assert attachment_key({"name": "uuid"}) == "uuid"
        assert attachment_key("uuid") == "uuid"

    def test_image_headers_match_site(self) -> None:
        assert IMAGE_HEADERS["Referer"] == f"{SITE_ORIGIN}/"
        assert "Mozilla/5.0" in IMAGE_HEADERS["User-Agent"]

    def test_every_real_image_node_resolves(self, images_fixture: dict) -> None:
        chapter = _chapter(images_fixture)
        index = resolve_attachments(chapter)

        for node in _all_image_nodes(chapter):
            for key in image_keys(node):
                assert key in index, f"{key} не найден во вложениях главы"
                assert absolute_url(index[key]).startswith(SITE_ORIGIN)


class TestCompression:
    def test_real_png_becomes_1280px_jpeg(self, real_png: bytes) -> None:
        asset = compress_image(real_png)

        with Image.open(io.BytesIO(asset.data)) as result:
            assert result.width == DEFAULT_MAX_WIDTH == 1280
            assert result.height == round(1582 * 1280 / 2210)
            assert result.format == "JPEG"
            assert result.mode == "RGB"

    def test_real_png_size_is_around_229_kb(self, real_png: bytes) -> None:
        asset = compress_image(real_png)
        size_kb = len(asset.data) / 1024

        assert 180_000 < len(asset.data) < 280_000, f"{size_kb:.0f} КБ"

    def test_source_png_is_much_larger(self, real_png: bytes) -> None:
        asset = compress_image(real_png)

        assert len(asset.data) < len(real_png) / 5

    def test_small_image_is_not_enlarged(self, real_png: bytes) -> None:
        with Image.open(io.BytesIO(real_png)) as opened:
            small = opened.convert("RGB").resize((800, 573)).copy()

        buffer = io.BytesIO()
        small.save(buffer, format="PNG")
        asset = compress_image(buffer.getvalue())

        with Image.open(io.BytesIO(asset.data)) as result:
            assert result.width == 800

    def test_alpha_becomes_white_not_black(self) -> None:
        source = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")

        asset = compress_image(buffer.getvalue())

        with Image.open(io.BytesIO(asset.data)) as result:
            assert result.mode == "RGB"
            assert result.convert("RGB").getpixel((5, 5)) == (255, 255, 255)

    def test_semi_transparent_pixels_blend_with_white(self) -> None:
        source = Image.new("RGBA", (4, 4), (0, 0, 0, 128))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")

        asset = compress_image(buffer.getvalue())

        with Image.open(io.BytesIO(asset.data)) as result:
            red, green, blue = result.getpixel((1, 1))

        assert 120 < red < 136, "полупрозрачный чёрный должен стать светло-серым"
        assert (red, green, blue) == pytest.approx((red, red, red), abs=2)

    def test_palette_transparency_is_flattened(self) -> None:
        source = Image.new("P", (4, 4))
        source.putpalette([255, 255, 255] + [0, 0, 0] * 255)
        source.info["transparency"] = 0
        buffer = io.BytesIO()
        source.save(buffer, format="PNG", transparency=0)

        asset = compress_image(buffer.getvalue())

        with Image.open(io.BytesIO(asset.data)) as result:
            assert result.mode == "RGB"

    def test_quality_setting_is_honoured(self, real_png: bytes) -> None:
        low = compress_image(real_png, quality=30)
        high = compress_image(real_png, quality=95)

        assert len(low.data) < len(high.data)


class TestNoCompression:
    def test_zero_limit_keeps_original_bytes(self, real_png: bytes) -> None:
        asset = filter_and_compress(real_png, max_image_mb=0)

        assert asset.data == real_png
        assert asset.compressed is False
        assert asset.mime == "image/png"

    def test_zero_limit_keeps_size_and_format(self, real_png: bytes) -> None:
        asset = keep_original(real_png)

        assert len(asset.data) == len(real_png)
        with Image.open(io.BytesIO(asset.data)) as image:
            assert image.format == "PNG"
            assert image.size == (2210, 1582)

    def test_jpeg_passthrough_detected(self, real_png: bytes) -> None:
        jpeg = compress_image(real_png).data

        assert keep_original(jpeg).mime == "image/jpeg"

    def test_small_image_below_limit_is_untouched(self, real_png: bytes) -> None:
        limit = len(real_png) / 1024 / 1024 + 1

        assert needs_compression(len(real_png), limit) is False
        assert needs_compression(len(real_png), 0) is False

    def test_image_above_limit_is_compressed(self, real_png: bytes) -> None:
        limit = len(real_png) / 1024 / 1024 - 1

        assert needs_compression(len(real_png), limit) is True


class TestFilenames:
    def test_epub_filename_format(self) -> None:
        assert epub_filename(1, "image/jpeg") == "Images/img_0001.jpg"
        assert epub_filename(42, "image/jpeg") == "Images/img_0042.jpg"

    def test_png_keeps_png_extension(self) -> None:
        assert epub_filename(7, "image/png") == "Images/img_0007.png"

    def test_filenames_are_unique(self) -> None:
        from ranobelib_epub.images.pipeline import ImageAsset

        assets = [
            ImageAsset(filename="", data=b"", source_url="", mime="image/jpeg")
            for _ in range(25)
        ]

        assigned = assign_filenames(assets)

        names = [asset.filename for asset in assigned]
        assert len(set(names)) == len(names)
        assert names[0] == "Images/img_0001.jpg"
        assert names[-1] == "Images/img_0025.jpg"

    def test_filenames_are_deterministic(self) -> None:
        from ranobelib_epub.images.pipeline import ImageAsset

        def build() -> list[str]:
            assets = [
                ImageAsset(filename="", data=b"", source_url="", mime="image/jpeg")
                for _ in range(3)
            ]
            return [asset.filename for asset in assign_filenames(assets)]

        assert build() == build()


class TestPlaceholder:
    def test_placeholder_is_plain_text(self) -> None:
        text = placeholder("uuid-1")

        assert "<" not in text
        assert "uuid-1" in text

    def test_missing_attachment_yields_valid_xhtml(self) -> None:
        document = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "До"}]},
                {"type": "image", "attrs": {"images": [{"image": "missing-uuid"}]}},
                {"type": "paragraph", "content": [{"type": "text", "text": "После"}]},
            ],
        }

        result = convert_document(document, resolver=lambda key: "")

        assert "изображение недоступно" in result.html
        assert "<p>До</p>" in result.html
        assert "<p>После</p>" in result.html
        assert "<img" not in result.html
        assert 'class="missing-image"' in result.html
        from ranobelib_epub.convert.tiptap import assert_well_formed

        assert_well_formed(result.html)

    def test_missing_image_is_recorded_for_report(self) -> None:
        entry = mark_missing("https://ranobelib.me/uploads/x.png", "сеть недоступна")

        assert entry.url == "https://ranobelib.me/uploads/x.png"
        assert entry.reason == "сеть недоступна"

    def test_unavailable_server_placeholder_has_url(self) -> None:
        url = "https://ranobelib.me/uploads/ranobe/1/chapters/2/x.png"

        assert url in placeholder(url)


def _chapter(fixture: dict) -> dict:
    data = fixture["data"]
    return data if isinstance(data, dict) else data[0]


def _all_image_nodes(chapter: dict) -> list[dict]:
    found: list[dict] = []

    def walk(node: dict) -> None:
        if node.get("type") == "image":
            found.append(node)
        for child in node.get("content") or []:
            if isinstance(child, dict):
                walk(child)

    walk(chapter["content"])
    return found


def _first_image_node(fixture: dict) -> dict:
    return _all_image_nodes(_chapter(fixture))[0]