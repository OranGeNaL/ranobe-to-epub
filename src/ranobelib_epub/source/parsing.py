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

from ..models import Attachment, Book, Chapter, ChapterContent, TranslationBranch
from .numbering import apply_numbering

#: Язык, который проставляется в `dc:language`, когда сайт не отдал `inLanguage`.
DEFAULT_LANGUAGE = "ru"


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


def parse_book(payload: Any) -> Book:
    """Собирает `Book` из ответа `GET /manga/{slug_url}`."""
    data = _as_dict(_data(payload))
    restriction = _as_dict(data.get("ageRestriction"))

    return Book(
        slug_url=_as_text(data.get("slug_url")) or "",
        book_id=_as_int(data.get("id")),
        name=_as_text(data.get("name")),
        rus_name=_as_text(data.get("rus_name")),
        author=_as_text(data.get("author")),
        cover=parse_cover(data.get("cover")),
        status=parse_status(data.get("status")),
        release_date=_as_text(data.get("releaseDateString")),
        year=_parse_year(_as_text(data.get("releaseDateString"))),
        genres=parse_genres(data.get("genres")),
        summary=_as_text(data.get("summary")),
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

    doc = _as_dict(data.get("content")) or None
    return ChapterContent(
        doc=doc,
        attachments=tuple(attachments),
        branch_id=_as_int(data.get("branch_id")),
        volume=volume if volume is not None else _as_int(data.get("volume")),
        number=number if number is not None else _as_text(data.get("number")),
    )
