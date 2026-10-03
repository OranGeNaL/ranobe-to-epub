"""Разбор текста на extended grapheme clusters (задача 9.3).

Стандартной библиотеки для кластеров в Python нет: `unicodedata` не знает про ZWJ и
вариативные селекторы. Поточечное удаление эмодзи оставляет осиротевшие `U+200D`,
`U+FE0F` и модификаторы тона кожи — невидимые символы, которые ломают разбивку на
слова в движке CrossPoint. Поэтому здесь минимальный разбор кластеров по правилам,
которые реально встречаются в тексте книг.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator

ZERO_WIDTH_JOINER = "‍"
VARIATION_SELECTORS = range(0xFE00, 0xFE10)
VARIATION_SELECTORS_SUPPLEMENT = range(0xE0100, 0xE01F0)
SKIN_TONE_MODIFIERS = range(0x1F3FB, 0x1F400)
KEYCAP = 0x20E3
REGIONAL_INDICATORS = range(0x1F1E6, 0x1F200)

#: Категории, которые присоединяются к предыдущему символу внутри кластера.
COMBINING_CATEGORIES = frozenset({"Mn", "Me", "Mc"})


def _joins_cluster(char: str, previous: str, regional_count: int) -> bool:
    """Присоединяется ли символ к текущему кластеру."""
    point = ord(char)

    if unicodedata.combining(char) or unicodedata.category(char) in COMBINING_CATEGORIES:
        return True
    if point in VARIATION_SELECTORS or point in VARIATION_SELECTORS_SUPPLEMENT:
        return True
    if point in SKIN_TONE_MODIFIERS or point == KEYCAP:
        return True
    if point == ord(ZERO_WIDTH_JOINER):
        return True
    if previous == ZERO_WIDTH_JOINER:
        # Символ после ZWJ — часть того же эмодзи: 👨‍👩‍👧.
        return True
    if ord(previous) in REGIONAL_INDICATORS and point in REGIONAL_INDICATORS:
        # Флаг — это пара regional indicators, а не два отдельных символа.
        return regional_count % 2 == 1
    return False


def iter_clusters(text: str) -> Iterator[str]:
    """Разбивает строку на extended grapheme clusters."""
    current: list[str] = []
    regional_count = 0

    for char in text:
        if not current:
            current.append(char)
            regional_count = 1 if ord(char) in REGIONAL_INDICATORS else 0
            continue

        if _joins_cluster(char, current[-1], regional_count):
            current.append(char)
            if ord(char) in REGIONAL_INDICATORS:
                regional_count += 1
            continue

        yield "".join(current)
        current = [char]
        regional_count = 1 if ord(char) in REGIONAL_INDICATORS else 0

    if current:
        yield "".join(current)


def cluster_lengths(text: str) -> list[int]:
    """Длины кластеров — для отчёта о числе удалённых символов."""
    return [len(cluster) for cluster in iter_clusters(text)]
