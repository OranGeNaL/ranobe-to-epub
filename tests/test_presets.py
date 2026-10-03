"""Тесты пресетов сжатия (задачи 1.1, 1.2)."""

from __future__ import annotations

import pytest

from ranobelib_epub.images.presets import (
    DEFAULT_PRESET,
    PRESETS,
    PresetError,
    preset_names,
    resolve_settings,
)


class TestPresetTable:
    def test_names_and_order(self) -> None:
        assert preset_names() == ("max-quality", "medium", "max-compression", "crosspoint")

    def test_values_match_specification(self) -> None:
        assert (PRESETS["max-quality"].max_width, PRESETS["max-quality"].quality) == (1600, 92)
        assert PRESETS["max-quality"].max_mb == 5.0
        assert (PRESETS["medium"].max_width, PRESETS["medium"].quality) == (1280, 80)
        assert PRESETS["medium"].max_mb == 0.5
        assert (PRESETS["max-compression"].max_width, PRESETS["max-compression"].quality) == (
            640,
            60,
        )
        assert PRESETS["max-compression"].max_mb == 0.5
        assert (PRESETS["crosspoint"].max_width, PRESETS["crosspoint"].quality) == (480, 80)
        assert PRESETS["crosspoint"].grayscale is True

    def test_default_is_medium(self) -> None:
        assert DEFAULT_PRESET == "medium"

    def test_only_crosspoint_is_grayscale(self) -> None:
        assert [name for name, preset in PRESETS.items() if preset.grayscale] == ["crosspoint"]


class TestResolveSettings:
    def test_preset_applies_its_values(self) -> None:
        settings = resolve_settings("crosspoint")

        assert settings.max_width == 480
        assert settings.quality == 80
        assert settings.max_mb == 0.5
        assert settings.grayscale is True
        assert settings.preset == "crosspoint"

    def test_manual_values_are_used_as_is(self) -> None:
        settings = resolve_settings(None, max_width=900, quality=55, max_mb=2.0)

        assert settings.max_width == 900
        assert settings.quality == 55
        assert settings.max_mb == 2.0
        assert settings.grayscale is False
        assert settings.preset is None

    def test_unspecified_manual_values_fall_back_to_medium(self) -> None:
        settings = resolve_settings(None, max_width=900)

        assert settings.max_width == 900
        assert settings.quality == 80
        assert settings.max_mb == 0.5

    def test_no_arguments_give_defaults(self) -> None:
        settings = resolve_settings(None)

        assert (settings.max_width, settings.quality, settings.max_mb) == (1280, 80, 0.5)
        assert settings.preset is None

    def test_unknown_preset_is_rejected(self) -> None:
        with pytest.raises(PresetError, match="неизвестный пресет"):
            resolve_settings("turbo")

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"max_width": 800},
            {"quality": 60},
            {"max_mb": 1.0},
        ],
    )
    def test_preset_conflicts_with_manual_parameters(self, kwargs: dict) -> None:
        with pytest.raises(PresetError, match="нельзя сочетать"):
            resolve_settings("medium", **kwargs)
