"""Нормализация HTML-содержимого главы в TipTap-документ.

Регрессия: сайт отдаёт часть глав (например, 26.1) готовой HTML-строкой, а не
ProseMirror-JSON. Без нормализации такая глава собиралась пустой — белые страницы.
"""

from __future__ import annotations

from ranobelib_epub.convert.html import attachment_key_from_url, html_to_document
from ranobelib_epub.convert.tiptap import convert_document
from ranobelib_epub.images.pipeline import collect_image_keys


class TestHtmlToDocument:
    def test_paragraphs_and_text(self) -> None:
        doc = html_to_document("<p>Первый</p><p>Второй</p>")

        assert doc["type"] == "doc"
        assert [node["type"] for node in doc["content"]] == ["paragraph", "paragraph"]
        assert doc["content"][0]["content"] == [{"type": "text", "text": "Первый"}]

    def test_marks_are_kept(self) -> None:
        doc = html_to_document("<p>a <strong>b</strong> <em>c</em></p>")

        marks = [
            mark
            for node in doc["content"][0]["content"]
            if node.get("type") == "text"
            for mark in (node.get("marks") or [])
        ]

        assert {"type": "bold"} in marks
        assert {"type": "italic"} in marks

    def test_image_becomes_node_with_attachment_key(self) -> None:
        doc = html_to_document('<p><img src="https://ranobelib.me/uploads/a/b/pic_9.png" /></p>')

        assert collect_image_keys(doc) == ["pic_9"]

    def test_convert_uses_resolver_for_image(self) -> None:
        doc = html_to_document('<p><img src="https://ranobelib.me/uploads/a/b/pic_9.png" /></p>')

        result = convert_document(doc, resolver=lambda key: f"../Images/{key}.jpg")

        assert '<img src="../Images/pic_9.jpg"' in result.html

    def test_br_becomes_hard_break(self) -> None:
        doc = html_to_document("<p>a<br />b</p>")

        kinds = [node["type"] for node in doc["content"][0]["content"]]

        assert kinds == ["text", "hardBreak", "text"]

    def test_empty_input(self) -> None:
        assert html_to_document("") == {"type": "doc", "content": []}
        assert html_to_document("   ") == {"type": "doc", "content": []}
        assert html_to_document(None) == {"type": "doc", "content": []}

    def test_attachment_key_from_url(self) -> None:
        assert attachment_key_from_url("https://x/u/a/pic_9.png") == "pic_9"
        assert attachment_key_from_url("/uploads/a/pic_9.PNG") == "pic_9"
        assert attachment_key_from_url("noext") == "noext"
