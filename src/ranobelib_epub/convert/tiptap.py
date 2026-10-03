"""Конвертер TipTap/ProseMirror JSON в XHTML (решение 3, задачи 7.1-7.7).

Что установлено по реальной разметке ranobelib.me: глава приходит как
`{"content": {"type": "doc", "content": [...]}}`, узлы `text` несут
`{"text": "...", "marks": [{"type": "bold"}, {"type": "italic", "attrs": {...}}]}`,
а у изображений `attrs.images` — это *список* файлов главы, а не одно изображение.
Поэтому `image` раскрывается в набор `<img>` по порядку файлов, а не в один тег.

Дизайн (решение 3): вход — чистые данные, результат — готовый XHTML-фрагмент плюс
предупреждения. Никаких замен подстрок в готовом HTML: неизвестные узлы и марки
пропускаются с предупреждением, текст главы при этом не теряется (7.6).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

_XHTML_NS = "http://www.w3.org/1999/xhtml"

#: Ключ вложения → `src` для `<img>`. Конвертер не знает ни про сжатие, ни про имена
#: файлов в EPUB: этим занимается конвейер иллюстраций, а преобразование остаётся чистым.
ImageResolver = Callable[[str], str]

PLACEHOLDER_TEMPLATE = "[изображение недоступно: {url}]"


def placeholder(key: str) -> str:
    """Текстовая заглушка вместо недоступной иллюстрации."""
    return PLACEHOLDER_TEMPLATE.format(url=key)

#: Узлы, которые не имеют представления в книге, но должны сохранять своё содержимое.
_CONTAINER_TAGS = {
    "doc": "div",
    "paragraph": "p",
    "heading": "h",
    "blockquote": "blockquote",
    "bulletList": "ul",
    "orderedList": "ol",
    "listItem": "li",
    "codeBlock": "pre",
    "hardBreak": "br",
    "horizontalRule": "hr",
    "paragraphSpacer": None,
}


@dataclass(slots=True)
class ConversionResult:
    """XHTML-фрагмент и предупреждения о деградации."""

    html: str
    warnings: list[str] = field(default_factory=list)


def _tag(name: str) -> str:
    return f"{{{_XHTML_NS}}}{name}"


def _indent(elem: ET.Element, level: int = 0) -> None:
    pad = "\n" + "  " * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + "  "
        for child in elem:
            _indent(child, level + 1)
        if not (elem[-1].tail or "").strip():
            elem[-1].tail = pad
    if level and not (elem.tail or "").strip():
        elem.tail = pad


def _serialize(elem: ET.Element) -> str:
    _indent(elem)
    body = ET.tostring(elem, encoding="unicode", xml_declaration=False)
    # Убираем префикс пространства имён: фрагмент вставляется в шаблон главы,
    # где `xmlns` уже объявлен один раз.
    return body.replace(f' xmlns="{_XHTML_NS}"', "")


def _render_children(
    node: dict, result: ConversionResult, resolver: ImageResolver | None = None
) -> str:
    parts = [
        _render_node(child, result, resolver)
        for child in node.get("content", []) or []
        if child
    ]
    return "".join(parts)


def _text_with_marks(node: dict, result: ConversionResult) -> str:
    text = node.get("text") or ""
    rendered = escape(text)
    if not rendered:
        return ""

    for mark in node.get("marks", []) or []:
        if not mark:
            continue
        kind = mark.get("type")
        attrs = mark.get("attrs") or {}
        if kind == "bold":
            rendered = f"<strong>{rendered}</strong>"
        elif kind == "italic":
            rendered = f"<em>{rendered}</em>"
        elif kind == "code":
            rendered = f"<code>{rendered}</code>"
        elif kind == "link":
            href = attrs.get("href") or ""
            if not href:
                result.warnings.append("марка link без href пропущена")
                continue
            rendered = f"<a href={quoteattr(href)}>{rendered}</a>"
        elif kind in {"subscript", "superscript"}:
            tag = "sub" if kind == "subscript" else "sup"
            rendered = f"<{tag}>{rendered}</{tag}>"
        elif kind == "underline":
            rendered = f"<span class=\"underline\">{rendered}</span>"
        elif kind == "strikethrough":
            rendered = f"<span class=\"strikethrough\">{rendered}</span>"
        elif kind == "highlight":
            color = attrs.get("color") or "yellow"
            safe_color = escape(color, {chr(34): "&quot;"})
            rendered = f'<span style="background-color: {safe_color}">{rendered}</span>'
        else:
            result.warnings.append(f"неизвестная марка «{kind}» пропущена")

    return rendered


def _render_image(node: dict, result: ConversionResult, resolver: ImageResolver | None) -> str:
    attrs = node.get("attrs") or {}
    files = attrs.get("images") or []
    if isinstance(files, (str, dict)):
        files = [files]
    if not files:
        result.warnings.append("узел image без attrs.images пропущен")
        return ""

    alt = escape(str(attrs.get("description") or attrs.get("alt") or attrs.get("title") or ""))
    out = []
    for item in files:
        key = item if isinstance(item, str) else str((item or {}).get("image") or "")
        if not key:
            continue
        src = resolver(key) if resolver else key
        if not src:
            # Заглушка — это текст, а не `src`: несуществующий путь в EPUB смотрелся бы
            # как сломанная картинка вместо честного объяснения.
            result.warnings.append(f"иллюстрация {key} недоступна, вставлена заглушка")
            out.append(f'<span class="missing-image">{escape(placeholder(key))}</span>')
            continue
        out.append(f'<img src={quoteattr(src)} alt={quoteattr(alt)} />')

    if not out:
        result.warnings.append("узел image без файлов пропущен")
    return "".join(out)


def _render_node(
    node: dict, result: ConversionResult, resolver: ImageResolver | None = None
) -> str:
    kind = node.get("type")
    attrs = node.get("attrs") or {}

    if kind == "text":
        return _text_with_marks(node, result)

    if kind == "image":
        return _render_image(node, result, resolver)

    if kind == "paragraphSpacer":
        return ""

    if kind not in _CONTAINER_TAGS:
        result.warnings.append(f"неизвестный узел «{kind}» пропущен вместе с содержимым")
        return _render_children(node, result, resolver)

    tag = _CONTAINER_TAGS[kind]

    if kind == "hardBreak":
        return "<br />"
    if kind == "horizontalRule":
        return "<hr />"
    if kind == "codeBlock":
        # TipTap codeBlock: attrs.language задаёт CSS-класс, чтобы подсветка в читалке
        # не ломалась на незнакомых языках.
        language = attrs.get("language")
        cls = f' class="language-{escape(str(language))}"' if language else ""
        return f"<pre><code{cls}>{_render_children(node, result, resolver)}</code></pre>"
    if kind == "heading":
        level = attrs.get("level") or 1
        name = f"h{max(1, min(int(level), 6))}"
        return f"<{name}>{_render_children(node, result, resolver)}</{name}>"

    if kind in {"doc", "blockquote"}:
        return _render_children(node, result, resolver)

    return f"<{tag}>{_render_children(node, result, resolver)}</{tag}>"


def convert_document(
    document: dict | None, resolver: ImageResolver | None = None
) -> ConversionResult:
    """Конвертирует TipTap-документ в XHTML-фрагмент.

    Принимает как сам узел `doc`, так и обёртку `{"content": {"type": "doc", ...}}`:
    `/chapters/{id}/content` отдаёт содержимое под ключом `content`.
    """
    result = ConversionResult(html="")
    if not document:
        return result

    node = document
    if node.get("type") != "doc" and isinstance(node.get("content"), dict):
        node = node["content"]

    if node.get("type") != "doc":
        result.warnings.append(f"документ имеет тип «{node.get('type')}» вместо doc")
        node = {"type": "doc", "content": [node]}

    result.html = _render_children(node, result, resolver)
    return result


def convert_to_fragment(document: dict | None, resolver: ImageResolver | None = None) -> str:
    """Только XHTML без предупреждений — для мест, где они уже учтены."""
    return convert_document(document, resolver).html


def assert_well_formed(fragment: str, wrapper: str = "body") -> None:
    """Проверяет well-formedness фрагмента (используется в тестах, 7.7)."""
    ET.fromstring(f'<{wrapper} xmlns="{_XHTML_NS}">{fragment}</{wrapper}>')