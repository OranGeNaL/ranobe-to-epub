"""Нормализация HTML-содержимого главы в TipTap-документ.

Часть глав `ranobelib.me` отдаётся не как ProseMirror-JSON, а как готовая
HTML-строка (поле `content` с тегами `<p>`/`<img>`). Конвертер ожидает словарь:
такие главы превращались в `None` и выходили пустыми белыми страницами.

Функция приводит обе формы к одному виду, поэтому дальше работает общий конвейер
(фильтрация символов, загрузка картинок, конвертация в XHTML). Картинки
преобразуются в узлы `image` с ключом вложения — имя файла из `src` без
расширения, — чтобы резолвер нашёл его среди `attachments[]`.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse

_BLOCK_TYPES = {
    "p": "paragraph",
    "div": "paragraph",
    "blockquote": "blockquote",
    "ul": "bulletList",
    "ol": "orderedList",
    "li": "listItem",
    "pre": "codeBlock",
}
_HEADING_LEVELS = {f"h{level}": level for level in range(1, 7)}
_MARKS = {
    "strong": "bold",
    "b": "bold",
    "em": "italic",
    "i": "italic",
    "code": "code",
    "s": "strikethrough",
    "strike": "strikethrough",
    "del": "strikethrough",
    "u": "underline",
}


def attachment_key_from_url(url: str) -> str:
    """Ключ вложения по URL картинки: имя файла без расширения.

    Вложения главы называются UUID или именем файла без расширения, и именно этот
    ключ указывают ProseMirror-узлы. Из `<img src=".../re-zero-26-4_1afn.png">`
    берётся `re-zero-26-4_1afn`.
    """
    name = urlparse(url).path.rsplit("/", 1)[-1]
    stem, _, _ = name.rpartition(".")
    return stem or name


class _DocumentBuilder(HTMLParser):
    """Строит TipTap-документ по потоку тегов, сохраняя текст и известные узлы."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.document: dict = {"type": "doc", "content": []}
        self._stack: list[dict] = [self.document]
        self._marks: list[str] = []

    def _append(self, node: dict) -> None:
        self._stack[-1].setdefault("content", []).append(node)

    def _block_node(self, tag: str) -> dict | None:
        if tag in _HEADING_LEVELS:
            return {"type": "heading", "attrs": {"level": _HEADING_LEVELS[tag]}}
        kind = _BLOCK_TYPES.get(tag)
        return {"type": kind} if kind else None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        mark = _MARKS.get(tag)
        if mark:
            self._marks.append(mark)
            return
        if tag == "br":
            self._append({"type": "hardBreak"})
            return
        if tag == "img":
            key = attachment_key_from_url(attributes.get("src") or "")
            if key:
                image = {"type": "image", "attrs": {"images": [{"image": key}]}}
                if attributes.get("alt"):
                    image["attrs"]["description"] = attributes["alt"]
                self._append(image)
            return
        node = self._block_node(tag)
        if node is not None:
            self._append(node)
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in _MARKS:
            if self._marks:
                self._marks.pop()
            return
        if (tag in _BLOCK_TYPES or tag in _HEADING_LEVELS) and len(self._stack) > 1:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        # Пробелы между блоками на верхнем уровне не несут смысла и только мусорят.
        if not data or (not data.strip() and len(self._stack) == 1):
            return
        node: dict = {"type": "text", "text": data}
        if self._marks:
            node["marks"] = [{"type": mark} for mark in self._marks]
        self._append(node)


def html_to_document(html: str | None) -> dict:
    """Преобразует HTML-строку главы в узел TipTap `doc`."""
    if not html or not html.strip():
        return {"type": "doc", "content": []}
    builder = _DocumentBuilder()
    builder.feed(html)
    builder.close()
    return builder.document
