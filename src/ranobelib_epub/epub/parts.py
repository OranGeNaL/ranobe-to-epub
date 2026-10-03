"""Метаданные и навигация EPUB 3 (задачи 10.2-10.6)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape, quoteattr

from ..models import Book

CONTAINER_PATH = "META-INF/container.xml"
PACKAGE_PATH = "EPUB/package.opf"
NAV_PATH = "EPUB/nav.xhtml"
NCX_PATH = "EPUB/toc.ncx"

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"

#: Пространство имён для URI вида `urn:uuid:...`, чтобы идентификатор был уникален.
BOOK_NAMESPACE = uuid.UUID("6f9c7f1e-1f6a-5f7a-9c3a-2f1a0d6b4c21")

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'
HEADER = XML_DECLARATION.rstrip("\n")


def book_identifier(book: Book) -> str:
    """Стабильный `dc:identifier`: одинаков для одной книги при любом числе сборок.

    Берётся `uuid5` от идентичности книги (`book_id` либо `slug_url`), поэтому файл
    не меняется от запуска к запуску — в отличие от `uuid4`, который ломал бы
    требование об идентификаторе и сравнение двух сборок в тестах.
    """
    identity = f"ranobelib:{book.book_id}" if book.book_id else f"ranobelib:{book.slug_url}"
    return f"urn:uuid:{uuid.uuid5(BOOK_NAMESPACE, identity)}"


def container_xml() -> str:
    """`META-INF/container.xml` — единственная точка входа в EPUB."""
    return (
        f'{HEADER}<container version="1.0" '
        f'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        f"  <rootfiles>\n"
        f'    <rootfile full-path="EPUB/package.opf" '
        f'media-type="application/oebps-package+xml"/>\n'
        f"  </rootfiles>\n"
        f"</container>\n"
    )


@dataclass(frozen=True, slots=True)
class ChapterEntry:
    """Глава для навигации: заголовок, файл и порядковый номер."""

    title: str
    href: str
    play_order: int


def _language_tag(value: str | None) -> str:
    return value or "ru"


def package_opf(
    book: Book,
    chapters: list[ChapterEntry],
    images: Iterable[tuple[str, str]] = (),
    cover_item: str | None = None,
    cover_media_type: str = "image/jpeg",
    build_date: date | None = None,
) -> str:
    """`EPUB/package.opf` с метаданными и манифестом (10.3, 10.4).

    В манифест обязаны попасть **все** файлы, на которые ссылаются главы: иначе
    epubcheck считает книгу повреждённой (`RSC-008`), даже если картинки лежат в архиве.
    """
    title = escape(book.title)
    identifier = escape(book_identifier(book))
    language = escape(_language_tag(book.in_language))
    stamp = (build_date or date.today()).isoformat()

    lines = [
        HEADER,
        f'<package xmlns="{_OPF_NS}" version="3.0" unique-identifier="bookid">',
        '  <metadata xmlns:dc="' + _DC_NS + '">',
        f"    <dc:identifier id=\"bookid\">{identifier}</dc:identifier>",
        f"    <dc:title>{title}</dc:title>",
        f"    <dc:language>{language}</dc:language>",
    ]

    if book.author:
        lines.append(f"    <dc:creator>{escape(book.author)}</dc:creator>")
    if book.summary:
        lines.append(f"    <dc:description>{escape(book.summary)}</dc:description>")
    if book.genres:
        joined = ", ".join(book.genres)
        lines.append(f'    <dc:subject>{escape(joined)}</dc:subject>')

    lines.append(f"    <dc:date>{stamp}</dc:date>")
    lines.append('    <meta property="dcterms:modified">' + f"{stamp}T00:00:00Z</meta>")

    if cover_item:
        lines.append('    <meta name="cover" content="cover-image"/>')

    lines.append("  </metadata>")
    lines.append("  <manifest>")
    lines.append('    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                 'properties="nav"/>')
    lines.append('    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

    for entry in chapters:
        lines.append(
            f'    <item id="ch{entry.play_order}" href={quoteattr(entry.href)} '
            'media-type="application/xhtml+xml"/>'
        )

    cover_set = {cover_item} if cover_item else set()
    for index, (href, mime) in enumerate(images, start=1):
        properties = ' properties="cover-image"' if href in cover_set else ""
        item_id = "cover-image" if href in cover_set else f"img{index}"
        lines.append(
            f'    <item id={quoteattr(item_id)} href={quoteattr(href)} '
            f'media-type={quoteattr(mime)}{properties}/>'
        )

    lines.append("  </manifest>")
    lines.append('  <spine toc="ncx">')
    for entry in chapters:
        lines.append(f'    <itemref idref="ch{entry.play_order}"/>')
    lines.append("  </spine>")
    lines.append("</package>\n")
    return "\n".join(lines)


def nav_xhtml(book: Book, chapters: list[ChapterEntry]) -> str:
    """`EPUB/nav.xhtml` с оглавлением всех глав в порядке сборки (10.5)."""
    items = "\n".join(
        f"        <li><a href={quoteattr(entry.href)}>{escape(entry.title)}</a></li>"
        for entry in chapters
    )
    return (
        f"{HEADER}<html xmlns=\"{_XHTML_NS}\" xmlns:epub=\"http://www.idpf.org/2007/ops\" "
        'xml:lang="' + escape(_language_tag(book.in_language)) + '">\n'
        "  <head>\n"
        "    <title>" + escape(book.title) + "</title>\n"
        "  </head>\n"
        "  <body>\n"
        '    <nav epub:type="toc" id="toc">\n'
        f"      <h1>{escape(book.title)}</h1>\n"
        "      <ol>\n"
        f"{items}\n"
        "      </ol>\n"
        "    </nav>\n"
        "  </body>\n"
        "</html>\n"
    )


def toc_ncx(chapters: list[ChapterEntry], book: Book) -> str:
    """`EPUB/toc.ncx` для старых читалок с тем же порядком глав (10.6)."""
    points = "\n".join(
        f'    <navPoint id="nav{entry.play_order}" playOrder="{entry.play_order}">\n'
        f"      <navLabel><text>{escape(entry.title)}</text></navLabel>\n"
        f"      <content src={quoteattr(entry.href)}/>\n"
        "    </navPoint>"
        for entry in chapters
    )
    identifier = escape(book_identifier(book))
    return (
        f"{HEADER}<ncx xmlns=\"{_NCX_NS}\" version=\"2005-1\">\n"
        "  <head>\n"
        f'    <meta name="dtb:uid" content={quoteattr(identifier)}/>\n'
        "    <meta name=\"dtb:depth\" content=\"1\"/>\n"
        "  </head>\n"
        f'  <docTitle><text>{escape(book.title)}</text></docTitle>\n'
        f'  <navMap id="navMap">\n{points}\n  </navMap>\n'
        "</ncx>\n"
    )


def chapter_document(title: str, fragment: str, language: str | None = None) -> str:
    """XHTML-страница главы: один `xmlns`, фрагмент вставляется как есть.

    Объявление пространства имён даётся ровно один раз на документ — иначе
    `xmlns:p` на каждом абзаце раздувает файл и ломает разбор в некоторых читалках.
    """
    return (
        f'{HEADER}<html xmlns="{_XHTML_NS}" '
        f'xml:lang="{escape(_language_tag(language))}" lang="{escape(_language_tag(language))}">\n'
        "  <head>\n"
        f"    <title>{escape(title)}</title>\n"
        '    <meta charset="utf-8"/>\n'
        "  </head>\n"
        "  <body>\n"
        f"{fragment}\n"
        "  </body>\n"
        "</html>\n"
    )