"""Профили набора символов CrossPoint (решение 13, задачи 9.1-9.2).

Источник истины — пресет `builtin` из прошивки CrossPoint
(`lib/EpdFont/scripts/fontconvert_sdcard.py`), который комментируется разработчиками
как «Matches the built-in font intervals from fontconvert.py exactly». Диапазоны
перенесены из того файла; при обновлении прошивки константу нужно пересверить —
автоматически получить список с устройства нельзя, сборка идёт на десктопе.

Нумерация версии: `builtin-2025-06` — снимок диапазонов на дату сверки. Она хранится
рядом с диапазонами, чтобы расхождение с прошивкой обнаруживалось осознанно, а не по
тихим «битым» символам в готовой книге.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

#: Версия снятого набора диапазонов.
CROSSPOINT_PRESET_VERSION = "builtin-2025-06"

#: Диапазоны пресета `builtin` (start, end включительно), как в CrossPoint.
BUILTIN_RANGES: tuple[tuple[int, int], ...] = (
    (0x0000, 0x007F),  # Basic Latin
    (0x0080, 0x00FF),  # Latin-1 Supplement
    (0x0100, 0x017F),  # Latin Extended-A
    (0x01A0, 0x01A1),  # Latin Extended-B (выборка)
    (0x01AF, 0x01B0),
    (0x01C4, 0x021F),  # Latin Extended-B (остаток)
    (0x0300, 0x036F),  # Combining Diacritical Marks
    (0x0400, 0x04FF),  # Cyrillic
    (0x1EA0, 0x1EF9),  # Latin Extended Additional
    (0x2000, 0x206F),  # General Punctuation
    (0x2070, 0x209F),  # Superscripts and Subscripts
    (0x20A0, 0x20CF),  # Currency Symbols
    (0x2190, 0x21FF),  # Arrows
    (0x2200, 0x22FF),  # Mathematical Operators
    (0xFB00, 0xFB06),  # Alphabetic Presentation Forms
)

#: Дополнительные диапазоны профиля `builtin+symbols`: буквенные символы, стрелки,
#: математика, псевдографика и значки — всё, что читатель обычно рисует в тексте.
SYMBOL_RANGES: tuple[tuple[int, int], ...] = (
    (0x2100, 0x2BFF),
    (0x2E00, 0x2E7F),
    (0x3000, 0x303F),  # CJK Symbols and Punctuation (точки, скобки)
)

#: Диапазоны профиля `builtin+symbols+cjk`: кану, кандзи и полноширинные формы.
CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3040, 0x30FF),  # Hiragana + Katakana
    (0x31F0, 0x31FF),
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xFF01, 0xFF60),  # Fullwidth Forms
    (0xFFE0, 0xFFE6),
)

#: Профиль `full` — весь Unicode, кроме управляющих символов C0/C1 и BOM.
FULL_RANGES: tuple[tuple[int, int], ...] = ((0x0020, 0xD7FF), (0xE000, 0x10FFFF))


@dataclass(frozen=True, slots=True)
class CharsetProfile:
    """Профиль набора символов: отсортированные диапазоны и быстрая проверка."""

    name: str
    ranges: tuple[tuple[int, int], ...]
    version: str = CROSSPOINT_PRESET_VERSION

    @property
    def starts(self) -> tuple[int, ...]:
        return tuple(start for start, _ in self.ranges)

    def supports(self, char: str) -> bool:
        """Поддерживается ли символ профилем."""
        point = ord(char)
        starts = self.starts
        index = bisect_right(starts, point) - 1
        if index < 0:
            return False
        return point <= self.ranges[index][1]

    def supports_cluster(self, cluster: str) -> bool:
        """Кластер поддерживается целиком, если поддержан каждый его символ.

        Частичное сохранение эмодзи бессмысленно: оставшийся ZWJ или модификатор тона
        кожи превращается в невидимый символ, который ломает разбивку на слова.
        """
        return all(self.supports(char) for char in cluster)


def _merge(*groups: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    """Объединяет и сливает пересекающиеся диапазоны."""
    merged: list[list[int]] = []
    for start, end in sorted(pair for group in groups for pair in group):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


BUILTIN = CharsetProfile("builtin", _merge(BUILTIN_RANGES))
BUILTIN_SYMBOLS = CharsetProfile("builtin+symbols", _merge(BUILTIN_RANGES, SYMBOL_RANGES))
BUILTIN_SYMBOLS_CJK = CharsetProfile(
    "builtin+symbols+cjk", _merge(BUILTIN_RANGES, SYMBOL_RANGES, CJK_RANGES)
)
FULL = CharsetProfile("full", FULL_RANGES, version="unicode-16.0")

PROFILES: dict[str, CharsetProfile] = {
    profile.name: profile for profile in (BUILTIN, BUILTIN_SYMBOLS, BUILTIN_SYMBOLS_CJK, FULL)
}

DEFAULT_PROFILE = "builtin"


def resolve_profile(name: str | None) -> CharsetProfile:
    """Профиль по имени; неизвестное имя даёт внятную ошибку со списком вариантов."""
    if not name:
        return BUILTIN
    try:
        return PROFILES[name]
    except KeyError as error:
        options = ", ".join(PROFILES)
        message = f"неизвестный профиль набора символов «{name}». Доступны: {options}"
        raise ValueError(message) from error
