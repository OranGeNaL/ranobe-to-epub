"""Иерархическая нумерация глав (решение 12).

Номер строится из полей сайта как `{volume}.{number}`, где `number` используется
дословно. Сайт кодирует в нём уровень иерархии: дробные значения — побочные истории и
интерлюдии, поэтому том 1 с `number=22.5` получает номер `1.22.5` и оказывается потомком
главы `1.22`.

`number_secondary` в номер не входит: он повторяет номер тома, и включение дало бы
`46.37.46` — уровня, которого в книге нет.
"""

from __future__ import annotations

from collections import defaultdict

from .models import Chapter, number_sort_key


def chapter_label(volume: int | str, number: int | str) -> str:
    """Номер главы `{volume}.{number}` с дословным использованием `number`."""
    number_text = str(number).strip()
    if not number_text:
        return str(volume)
    return f"{volume}.{number_text}"


def sort_chapters(chapters: list[Chapter]) -> list[Chapter]:
    """Сортирует главы по числовой иерархии, сохраняя порядок при равных ключах.

    Python-сортировка устойчива, поэтому главы с одинаковым `(volume, number)` остаются
    в исходном порядке и ни одна не теряется.
    """
    return sorted(chapters, key=lambda chapter: chapter.sort_key)


def assign_labels(chapters: list[Chapter]) -> dict[int, str]:
    """Проставляет `chapter.label` всем главам и возвращает метки по `chapter.id`.

    Коллизии номеров разрешаются порядковым суффиксом (`1.25`, `1.25-2`, `1.25-3`).
    На реальных данных книги коллизий нет — все 735 пар `(volume, number)` уникальны, —
    но молча подменять две главы одной меткой нельзя, поэтому случай обрабатывается явно.
    """
    counters: dict[str, int] = defaultdict(int)
    labels: dict[int, str] = {}

    for chapter in chapters:
        base = chapter_label(chapter.volume, chapter.number)
        counters[base] += 1
        label = base if counters[base] == 1 else f"{base}-{counters[base]}"
        chapter.label = label
        labels[chapter.id] = label

    return labels


def apply_numbering(chapters: list[Chapter]) -> list[Chapter]:
    """Сортирует главы и проставляет номера. Основная точка входа для сборки."""
    ordered = sort_chapters(chapters)
    assign_labels(ordered)
    return ordered


def chapter_sort_key(chapter: Chapter) -> tuple[int, ...]:
    return number_sort_key(chapter.volume, chapter.number)
