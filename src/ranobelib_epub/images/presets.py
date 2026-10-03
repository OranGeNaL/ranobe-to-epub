"""Пресеты сжатия иллюстраций (решение image-compression-presets).

Таблица пресетов — поведенческий контракт: значения перечислены в спеке
`epub-build` («Загрузка и сжатие иллюстраций»). Здесь же живёт единственное
правило разрешения: пресет и ручные параметры взаимоисключающи, а незаданные
ручные параметры падают на значения пресета `medium`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ImagePreset:
    """Именованный профиль сжатия."""

    name: str
    max_width: int
    quality: int
    max_mb: float
    grayscale: bool = False


PRESETS: dict[str, ImagePreset] = {
    "max-quality": ImagePreset("max-quality", max_width=1600, quality=92, max_mb=5.0),
    "medium": ImagePreset("medium", max_width=1280, quality=80, max_mb=0.5),
    "max-compression": ImagePreset("max-compression", max_width=640, quality=60, max_mb=0.5),
    "crosspoint": ImagePreset("crosspoint", max_width=480, quality=80, max_mb=0.5, grayscale=True),
}

DEFAULT_PRESET = "medium"


class PresetError(ValueError):
    """Неизвестный пресет или конфликт с ручными параметрами сжатия."""


@dataclass(frozen=True, slots=True)
class ResolvedSettings:
    """Эффективные настройки сжатия после разрешения пресета/ручного режима."""

    max_width: int
    quality: int
    max_mb: float
    grayscale: bool = False
    preset: str | None = None


def preset_names() -> tuple[str, ...]:
    """Имена пресетов в порядке объявления (для CLI и TUI)."""
    return tuple(PRESETS)


def resolve_settings(
    preset: str | None,
    *,
    max_width: int | None = None,
    quality: int | None = None,
    max_mb: float | None = None,
) -> ResolvedSettings:
    """Разрешает настройки сжатия из пресета или ручных значений.

    Пресет `medium` без ручных параметров даёт прежние значения по умолчанию.
    Конфликт пресета с любым ручным параметром — ошибка: смешивать их нельзя,
    иначе результат зависел бы от порядка применения.
    """
    if preset is not None:
        if preset not in PRESETS:
            options = ", ".join(PRESETS)
            raise PresetError(f"неизвестный пресет «{preset}». Доступны: {options}")
        manual = {
            "--max-image-width": max_width,
            "--quality": quality,
            "--max-image-mb": max_mb,
        }
        conflicting = [name for name, value in manual.items() if value is not None]
        if conflicting:
            raise PresetError(
                "пресет нельзя сочетать с ручными параметрами сжатия: "
                + ", ".join(conflicting)
            )
        chosen = PRESETS[preset]
        return ResolvedSettings(
            max_width=chosen.max_width,
            quality=chosen.quality,
            max_mb=chosen.max_mb,
            grayscale=chosen.grayscale,
            preset=preset,
        )

    fallback = PRESETS[DEFAULT_PRESET]
    return ResolvedSettings(
        max_width=fallback.max_width if max_width is None else max_width,
        quality=fallback.quality if quality is None else quality,
        max_mb=fallback.max_mb if max_mb is None else max_mb,
        grayscale=False,
        preset=None,
    )
