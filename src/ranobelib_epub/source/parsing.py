"""Разбор ответов Mangalib API в модели (задачи 4.1-4.6).

Все эндпоинты отдают обёртку `{"data": ...}`, поэтому каждая функция начинает с
извлечения `data`. Формы полей установлены по реальным ответам `ranobelib.me` и
зафиксированы в фикстурах `tests/fixtures`:

- `/manga/{slug_url}` → `data` с `cover` и `status` в виде объектов, а не строк;
- `/manga/{slug_url}/chapters` → `data` является **списком** глав, пагинации нет;
- ветка перевода определяется полем **`branch_id`**, а не `id` (последнее совпадает с
  идентификатором главы);
- вложение главы — `attachments[]` с `name` (uuid без расширения) и относительным `url`.

Отсутствие поля не является ошибкой: модель получает `None`, и сборка продолжается
(требование «Получение метаданных книги»).
"""

from __future__ import annotations

from typing import Any

from ..convert.html import html_to_document
from ..models import Attachment, Book, Chapter, ChapterContent, Cover, TranslationBranch
from .numbering import apply_numbering

#: Язык, который проставляется в `dc:language`, когда сайт не отдал `inLanguage`.
DEFAULT_LANGUAGE = "ru"

#: Приоритет вариантов размера для обложек карусели (решение 2 design.md).
_COVER_SIZE_ORDER = ("orig", "default", "md", "thumbnail")


def _data(payload: Any) -> Any:
    """Достаёт `data` из обёртки ответа API."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_year(release_date: str | None) -> int | None:
    """Год выхода из строки вида `2012 г.`; при отсутствии — None."""
    if not release_date:
        return None
    digits = "".join(ch for ch in release_date if ch.isdigit())
    return int(digits) if digits else None


def parse_cover(raw: Any) -> str | None:
    """Обложка приходит объектом: `filename`, `thumbnail`, `default`, `md`, ...

    `default` — полноразмерное изображение, `thumbnail` — уменьшенное; при отсутствии
    `default` берётся `thumbnail`.
    """
    cover = _as_dict(raw)
    if not cover:
        return None
    return _as_text(cover.get("default")) or _as_text(cover.get("thumbnail"))


def parse_status(raw: Any) -> str | None:
    """Статус приходит объектом `{id, label}`; используется человекочитаемая метка.

    Строковая форма принимается тоже: поле обязано быть заполнено всегда, а форма
    ответа может измениться при обновлении фронтенда.
    """
    if isinstance(raw, str):
        return _as_text(raw)
    status = _as_dict(raw)
    if not status:
        return None
    return _as_text(status.get("label")) or _as_text(status.get("name"))


def parse_genres(raw: Any) -> tuple[str, ...]:
    """Жанры приходят как массив объектов с `title`; у части книг поле равно `null`."""
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = _as_text(item.get("title") or item.get("name"))
        else:
            name = _as_text(item)
        if name:
            names.append(name)
    return tuple(names)


def parse_author(raw: Any) -> str | None:
    """Автор из `authors[]`: несколько имён объединяются через `, `.

    Поле `author` на карточке пустое, а реальные имена приходят только с расширением
    `fields[]=authors` как список объектов `{name, rus_name, ...}`.
    """
    if not isinstance(raw, list):
        return None
    names: list[str] = []
    for item in raw:
        author = _as_dict(item)
        name = _as_text(author.get("name")) or _as_text(author.get("rus_name"))
        if name:
            names.append(name)
    return ", ".join(names) if names else None


def summary_text(raw: Any) -> str | None:
    """Описание из ProseMirror-документа `summary` в виде обычного текста.

    Узлы-контейнеры (абзацы, заголовки) разделяются пробелом, вся разметка и
    повторные пробелы убираются — результат идёт в `dc:description`.
    """
    if isinstance(raw, str):
        text = raw
    else:
        doc = _as_dict(raw)
        if not doc:
            return None
        text = "".join(_summary_text_parts(doc))
    text = " ".join(text.split())
    return text or None


def _summary_text_parts(node: Any) -> list[str]:
    """Рекурсивный обход ProseMirror-узлов: текстовые узлы и разделители блоков."""
    if not isinstance(node, dict):
        return []
    if node.get("type") == "text":
        text = str(node.get("text") or "")
        return [text] if text else []
    parts: list[str] = []
    for child in node.get("content") or []:
        parts.extend(_summary_text_parts(child))
    if parts and node.get("type") in {"paragraph", "heading", "blockquote", "listItem"}:
        parts.append(" ")
    return parts


def parse_covers(payload: Any) -> tuple[Cover, ...]:
    """Список обложек карусели, упорядоченный по `order`.

    Метка — `info`, при пустом значении — `Том {order + 1}`. Сломанные записи и ответы
    без списка пропускаются: сборка не останавливается (требование «Карусель пуста или
    недоступна»).
    """
    raw = _data(payload)
    if not isinstance(raw, list):
        return ()
    covers: list[Cover] = []
    for item in raw:
        data = _as_dict(item)
        cover_id = _as_int(data.get("id"))
        if cover_id is None:
            continue
        order = _as_int(data.get("order")) or 0
        info = _as_text(data.get("info"))
        covers.append(
            Cover(
                id=cover_id,
                order=order,
                label=info or f"Том {order + 1}",
                url=_cover_variant_url(data.get("cover")),
            )
        )
    covers.sort(key=lambda cover: (cover.order, cover.id))
    return tuple(covers)


def _cover_variant_url(raw: Any) -> str | None:
    """URL обложки по приоритету `orig → default → md → thumbnail`."""
    cover = _as_dict(raw)
    for key in _COVER_SIZE_ORDER:
        url = _as_text(cover.get(key))
        if url:
            return url
    return None


def parse_book(payload: Any) -> Book:
    """Собирает `Book` из ответа `GET /manga/{slug_url}`."""
    data = _as_dict(_data(payload))
    restriction = _as_dict(data.get("ageRestriction"))

    return Book(
        slug_url=_as_text(data.get("slug_url")) or "",
        book_id=_as_int(data.get("id")),
        name=_as_text(data.get("name")),
        rus_name=_as_text(data.get("rus_name")),
        author=parse_author(data.get("authors")) or _as_text(data.get("author")),
        cover=parse_cover(data.get("cover")),
        status=parse_status(data.get("status")),
        release_date=_as_text(data.get("releaseDateString")),
        year=_parse_year(_as_text(data.get("releaseDateString"))),
        genres=parse_genres(data.get("genres")),
        summary=summary_text(data.get("summary")),
        in_language=_as_text(data.get("inLanguage")),
        age_restriction_id=_as_int(restriction.get("id")),
        age_restriction_label=_as_text(restriction.get("label")),
    )


def parse_branches(raw: Any) -> tuple[TranslationBranch, ...]:
    """Ветки перевода главы.

    Идентификатор ветки — `branch_id`: поле `id` внутри объекта ветки совпадает с
    идентификатором главы, и его использование привело бы к запросу чужой ветки.
    """
    if not isinstance(raw, list):
        return ()
    branches: list[TranslationBranch] = []
    for item in raw:
        data = _as_dict(item)
        branch_id = _as_int(data.get("branch_id"))
        if branch_id is None:
            continue
        teams = tuple(
            name
            for name in (_as_text(_as_dict(team).get("name")) for team in data.get("teams") or [])
            if name
        )
        branches.append(
            TranslationBranch(
                id=branch_id,
                name=_as_text(data.get("name")),
                teams=teams,
            )
        )
    return tuple(branches)


def parse_chapters(payload: Any) -> list[Chapter]:
    """Разбирает список глав и сразу применяет нумерацию (решение 12).

    `data` эндпоинта `/chapters` — сам список глав; обёртка с ключом `chapters` также
    принимается, чтобы не сломаться при смене формы ответа.
    """
    data = _data(payload)
    raw = data.get("chapters") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []

    chapters: list[Chapter] = []
    for item in raw:
        data = _as_dict(item)
        chapter_id = _as_int(data.get("id"))
        if chapter_id is None:
            continue
        chapters.append(
            Chapter(
                id=chapter_id,
                volume=_as_int(data.get("volume")) or 0,
                number=str(data.get("number", "0")),
                name=_as_text(data.get("name")) or "",
                branches=parse_branches(data.get("branches")),
                number_secondary=_as_int(data.get("number_secondary")),
                expired_type=_as_int(data.get("expired_type")),
            )
        )

    return apply_numbering(chapters)


def parse_chapter_content(
    payload: Any,
    *,
    volume: int | None = None,
    number: str | None = None,
) -> ChapterContent:
    """Разбирает ответ `GET .../chapter` в `ChapterContent`.

    Ранобэ отдаёт иллюстрации в `attachments`, манга — в `pages`; поддерживаются оба
    ключа (открытый вопрос design.md). `name` вложения — uuid без расширения, именно
    его ProseMirror-узел `image` указывает в `attrs.images[].image`.
    """
    data = _as_dict(_data(payload))

    raw_attachments = data.get("attachments")
    if not isinstance(raw_attachments, list):
        raw_attachments = data.get("pages") if isinstance(data.get("pages"), list) else []

    attachments: list[Attachment] = []
    for item in raw_attachments or []:
        item_data = _as_dict(item)
        name = _as_text(item_data.get("name"))
        if not name:
            continue
        attachments.append(
            Attachment(
                name=name,
                url=_as_text(item_data.get("url")) or _as_text(item_data.get("originalUrl")) or "",
                width=_as_int(item_data.get("width")),
                height=_as_int(item_data.get("height")),
            )
        )

    raw_content = data.get("content")
    if isinstance(raw_content, str):
        # Часть глав отдаётся готовой HTML-строкой, а не ProseMirror-JSON; без
        # нормализации такая глава выходила пустой (белые страницы).
        doc = html_to_document(raw_content)
    else:
        doc = _as_dict(raw_content) or None
    return ChapterContent(
        doc=doc,
        attachments=tuple(attachments),
        branch_id=_as_int(data.get("branch_id")),
        volume=volume if volume is not None else _as_int(data.get("volume")),
        number=number if number is not None else _as_text(data.get("number")),
    )
