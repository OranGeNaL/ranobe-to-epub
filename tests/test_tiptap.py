"""Тесты конвертера TipTap → XHTML (задачи 7.1-7.7)."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ranobelib_epub.convert.tiptap import assert_well_formed, convert_document

FIXTURES = Path(__file__).parent / "fixtures"


def documents_from(payload: dict) -> list[dict]:
    """TipTap-документы фикстуры: `data.content` либо список глав с `content`.

    Реальный API отдаёт и одиночную главу, и список глав, поэтому фикстуры тоже
    приходят в обеих формах.
    """
    data = payload.get("data")
    if isinstance(data, list):
        return [item["content"] for item in data if isinstance(item.get("content"), dict)]
    content = data.get("content") if isinstance(data, dict) else None
    return [content] if isinstance(content, dict) else []


def doc(*nodes: dict) -> dict:
    return {"type": "doc", "content": list(nodes)}


def text(value: str, *marks: dict) -> dict:
    return {"type": "text", "text": value, "marks": list(marks)}


def html_of(node: dict) -> str:
    return convert_document(node).html


class TestBasicNodes:
    def test_plain_text_chapter_keeps_paragraph_count(self) -> None:
        document = doc(
            {"type": "paragraph", "content": [text("Первый абзац")]},
            {"type": "paragraph", "content": [text("Второй абзац")]},
        )

        assert html_of(document) == "<p>Первый абзац</p><p>Второй абзац</p>"

    def test_heading_level_is_preserved(self) -> None:
        document = doc({"type": "heading", "attrs": {"level": 2}, "content": [text("Глава 1")]})

        assert html_of(document) == "<h2>Глава 1</h2>"

    @pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
    def test_all_heading_levels(self, level: int) -> None:
        document = doc({"type": "heading", "attrs": {"level": level}, "content": [text("T")]})

        assert html_of(document) == f"<h{level}>T</h{level}>"

    def test_heading_level_out_of_range_is_clamped(self) -> None:
        too_deep = doc({"type": "heading", "attrs": {"level": 9}, "content": [text("T")]})
        too_shallow = doc({"type": "heading", "attrs": {"level": 0}, "content": [text("T")]})

        assert "<h6>" in html_of(too_deep)
        assert "<h1>" in html_of(too_shallow)

    def test_empty_document(self) -> None:
        assert html_of(doc()) == ""

    def test_wrapper_content_key_is_unwrapped(self) -> None:
        wrapped = {"content": doc({"type": "paragraph", "content": [text("A")]})}

        assert html_of(wrapped) == "<p>A</p>"


class TestLeafNodes:
    def test_hard_break(self) -> None:
        content = [text("a"), {"type": "hardBreak"}, text("b")]
        document = doc({"type": "paragraph", "content": content})

        assert html_of(document) == "<p>a<br />b</p>"

    def test_horizontal_rule(self) -> None:
        assert html_of(doc({"type": "horizontalRule"})) == "<hr />"

    def test_blockquote(self) -> None:
        document = doc({"type": "blockquote", "content": [text("Цитата")]})

        assert html_of(document) == "Цитата"


class TestLists:
    def test_bullet_list(self) -> None:
        document = doc(
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [text("один")]},
                    {"type": "listItem", "content": [text("два")]},
                ],
            }
        )

        assert html_of(document) == "<ul><li>один</li><li>два</li></ul>"

    def test_ordered_list(self) -> None:
        document = doc(
            {
                "type": "orderedList",
                "content": [{"type": "listItem", "content": [text("один")]}],
            }
        )

        assert html_of(document) == "<ol><li>один</li></ol>"

    def test_nested_lists(self) -> None:
        document = doc(
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            text("внешний"),
                            {
                                "type": "bulletList",
                                "content": [{"type": "listItem", "content": [text("вложенный")]}],
                            },
                        ],
                    }
                ],
            }
        )

        assert html_of(document) == "<ul><li>внешний<ul><li>вложенный</li></ul></li></ul>"

    def test_ordered_list_start_attribute_is_dropped_safely(self) -> None:
        document = doc(
            {
                "type": "orderedList",
                "attrs": {"start": 3},
                "content": [{"type": "listItem", "content": [text("x")]}],
            }
        )

        assert html_of(document) == "<ol><li>x</li></ol>"


class TestCodeBlock:
    def test_plain_code_block(self) -> None:
        document = doc({"type": "codeBlock", "content": [text("print(1)")]})

        assert html_of(document) == "<pre><code>print(1)</code></pre>"

    def test_code_block_language_becomes_class(self) -> None:
        document = doc(
            {"type": "codeBlock", "attrs": {"language": "python"}, "content": [text("x = 1")]}
        )

        assert html_of(document) == '<pre><code class="language-python">x = 1</code></pre>'

    def test_code_block_content_is_escaped(self) -> None:
        document = doc({"type": "codeBlock", "content": [text("a < b && c")]})

        assert "&lt;" in html_of(document)
        assert_well_formed(html_of(document))


class TestMarks:
    def test_bold(self) -> None:
        assert html_of(doc({"type": "paragraph", "content": [text("x", {"type": "bold"})]})) == (
            "<p><strong>x</strong></p>"
        )

    def test_italic(self) -> None:
        assert html_of(doc({"type": "paragraph", "content": [text("x", {"type": "italic"})]})) == (
            "<p><em>x</em></p>"
        )

    def test_code_mark(self) -> None:
        assert html_of(doc({"type": "paragraph", "content": [text("x", {"type": "code"})]})) == (
            "<p><code>x</code></p>"
        )

    def test_link(self) -> None:
        document = doc(
            {
                "type": "paragraph",
                "content": [
                    text("сайт", {"type": "link", "attrs": {"href": "https://example.org"}})
                ],
            }
        )

        assert html_of(document) == '<p><a href="https://example.org">сайт</a></p>'

    def test_two_marks_on_one_node(self) -> None:
        document = doc(
            {
                "type": "paragraph",
                "content": [text("оба", {"type": "bold"}, {"type": "italic"})],
            }
        )

        result = html_of(document)

        assert "<strong>" in result
        assert "<em>" in result
        assert_well_formed(result)

    def test_link_without_href_is_skipped_with_warning(self) -> None:
        result = convert_document(
            doc({"type": "paragraph", "content": [text("текст", {"type": "link"})]})
        )

        assert result.html == "<p>текст</p>"
        assert result.warnings

    def test_subscript_and_superscript(self) -> None:
        sub = html_of(doc({"type": "paragraph", "content": [text("2", {"type": "subscript"})]}))
        sup = html_of(doc({"type": "paragraph", "content": [text("3", {"type": "superscript"})]}))

        assert sub == "<p><sub>2</sub></p>"
        assert sup == "<p><sup>3</sup></p>"


class TestEscaping:
    @pytest.mark.parametrize("raw", ["<", ">", "&"])
    def test_dangerous_characters_are_escaped(self, raw: str) -> None:
        document = doc({"type": "paragraph", "content": [text(f"a{raw}b")]})

        result = html_of(document)

        assert f"a{raw}b" not in result
        assert_well_formed(result)

    def test_script_tag_in_text_is_escaped(self) -> None:
        document = doc({"type": "paragraph", "content": [text("<script>alert(1)</script>")]})

        result = html_of(document)

        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_quote_in_text_is_escaped(self) -> None:
        document = doc({"type": "paragraph", "content": [text('он сказал "привет"')]})

        assert_well_formed(html_of(document))

    def test_link_href_cannot_break_out_of_attribute(self) -> None:
        """Кавычка в href не должна порождать второй атрибут."""
        hostile = 'a" onmouseover="alert(1)'
        mark = {"type": "link", "attrs": {"href": hostile}}
        document = doc({"type": "paragraph", "content": [text("x", mark)]})

        result = html_of(document)
        root = ET.fromstring(f'<body xmlns="http://www.w3.org/1999/xhtml">{result}</body>')
        link = root[0][0]

        assert link.attrib == {"href": hostile}, "href остался одним атрибутом"
        assert "onmouseover" not in link.attrib


class TestImages:
    def test_image_expands_to_all_files(self) -> None:
        document = doc(
            {
                "type": "image",
                "attrs": {"images": [{"image": "a"}, {"image": "b"}]},
            }
        )

        assert html_of(document) == '<img src="a" alt="" /><img src="b" alt="" />'

    def test_image_alt_comes_from_description(self) -> None:
        document = doc(
            {"type": "image", "attrs": {"images": [{"image": "a"}], "description": "Схема"}}
        )

        assert html_of(document) == '<img src="a" alt="Схема" />'

    def test_image_alt_is_escaped(self) -> None:
        document = doc(
            {"type": "image", "attrs": {"images": [{"image": "a"}], "description": 'a"b'}}
        )

        assert_well_formed(html_of(document))

    def test_resolver_replaces_key_with_epub_path(self) -> None:
        document = doc({"type": "image", "attrs": {"images": [{"image": "abc"}]}})

        html = convert_document(document, resolver=lambda key: "Images/img_0001.jpg").html

        assert html == '<img src="Images/img_0001.jpg" alt="" />'

    def test_resolver_returning_empty_gives_placeholder(self) -> None:
        document = doc({"type": "image", "attrs": {"images": [{"image": "abc"}]}})

        result = convert_document(document, resolver=lambda key: "")

        assert "изображение недоступно" in result.html
        assert result.warnings
        assert_well_formed(result.html)

    def test_image_without_files_warns(self) -> None:
        result = convert_document(doc({"type": "image", "attrs": {}}))

        assert result.html == ""
        assert result.warnings

    def test_single_string_image(self) -> None:
        document = doc({"type": "image", "attrs": {"images": "a"}})

        assert html_of(document) == '<img src="a" alt="" />'


class TestGracefulDegradation:
    def test_unknown_node_keeps_children_text(self) -> None:
        document = doc({"type": "youtubeEmbed", "attrs": {"videoId": "abc"}})

        result = convert_document(document)

        assert "youtubeEmbed" in result.warnings[0]
        assert result.html == ""

    def test_unknown_container_node_keeps_inner_text(self) -> None:
        document = doc(
            {
                "type": "callout",
                "content": [{"type": "paragraph", "content": [text("внутренний текст")]}],
            }
        )

        result = convert_document(document)

        assert "<p>внутренний текст</p>" in result.html
        assert result.warnings

    def test_unknown_mark_keeps_text(self) -> None:
        document = doc({"type": "paragraph", "content": [text("текст", {"type": "glitter"})]})

        result = convert_document(document)

        assert result.html == "<p>текст</p>"
        assert "glitter" in result.warnings[0]

    def test_unknown_mark_alongside_known_keeps_both_reporting(self) -> None:
        document = doc(
            {
                "type": "paragraph",
                "content": [text("t", {"type": "bold"}, {"type": "glitter"})],
            }
        )

        result = convert_document(document)

        assert "<strong>t</strong>" in result.html
        assert result.warnings


class TestWellFormedness:
    @pytest.mark.parametrize(
        "name", ["chapter.json", "chapter_rich.json", "chapter_images.json", "chapter_marks.json"]
    )
    def test_real_fixtures_are_well_formed(self, name: str) -> None:
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))

        fragments = [convert_document(doc).html for doc in documents_from(payload)]

        assert fragments, f"{name}: разметка не найдена в фикстуре"
        for fragment in fragments:
            if fragment:
                assert_well_formed(fragment)

    def test_full_fixture_set_passes(self) -> None:
        checked = 0
        for path in sorted(FIXTURES.glob("chapter*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for document in documents_from(payload):
                assert_well_formed(convert_document(document).html)
                checked += 1

        assert checked > 0, "фикстуры глав не найдены"

    def test_parsed_root_is_xhtml_paragraph(self) -> None:
        document = doc({"type": "paragraph", "content": [text("Проверка")]})

        root = ET.fromstring(
            '<body xmlns="http://www.w3.org/1999/xhtml">' + html_of(document) + "</body>"
        )

        assert root.tag == "{http://www.w3.org/1999/xhtml}body"
        assert root[0].tag == "{http://www.w3.org/1999/xhtml}p"
        assert root[0].text == "Проверка"
