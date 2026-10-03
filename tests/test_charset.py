"""Тесты фильтра символов CrossPoint (задачи 9.1-9.7)."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from ranobelib_epub.charset.filter import (
    apply_to_report,
    filter_document,
    filter_text,
)
from ranobelib_epub.charset.graphemes import iter_clusters
from ranobelib_epub.charset.profiles import (
    BUILTIN,
    BUILTIN_SYMBOLS,
    BUILTIN_SYMBOLS_CJK,
    CROSSPOINT_PRESET_VERSION,
    FULL,
    PROFILES,
    resolve_profile,
)
from ranobelib_epub.convert.tiptap import assert_well_formed, convert_document
from ranobelib_epub.models import Report

FIXTURES = Path(__file__).parent / "fixtures"


class TestBuiltinRanges:
    """Диапазоны пресета `builtin` (9.1)."""

    def test_preset_version_is_declared(self) -> None:
        assert CROSSPOINT_PRESET_VERSION.startswith("builtin-")

    @pytest.mark.parametrize("char", ["Ѐ", "Ё", "Ж", "џ", "Ё", "\u0401"])
    def test_cyrillic_supported(self, char: str) -> None:
        assert BUILTIN.supports(char)

    def test_full_cyrillic_block(self) -> None:
        assert all(BUILTIN.supports(chr(point)) for point in range(0x0400, 0x0500))

    def test_general_punctuation(self) -> None:
        assert all(BUILTIN.supports(chr(point)) for point in range(0x2000, 0x2070))

    def test_arrows(self) -> None:
        assert all(BUILTIN.supports(chr(point)) for point in range(0x2190, 0x2200))

    def test_math_operators(self) -> None:
        assert all(BUILTIN.supports(chr(point)) for point in range(0x2200, 0x2300))

    def test_basic_latin_and_cyrillic_text_survives(self) -> None:
        cleaned, stats = filter_text("Привет, мир! — «Цитата» 100%", "builtin")

        assert cleaned == "Привет, мир! — «Цитата» 100%"
        assert stats.removed_clusters == 0

    def test_numero_sign_is_not_in_builtin(self) -> None:
        """`№` — U+2116, это Letterlike Symbols: в `builtin` его нет, в `+symbols` есть."""
        assert BUILTIN.supports("№") is False
        assert BUILTIN_SYMBOLS.supports("№") is True

    def test_emoji_block_excluded(self) -> None:
        assert not BUILTIN.supports("\U0001f300")
        assert not BUILTIN.supports("\U0001faff")

    def test_box_drawing_excluded(self) -> None:
        assert not BUILTIN.supports("─")
        assert not BUILTIN.supports("│")

    def test_math_alphanumeric_excluded(self) -> None:
        assert not BUILTIN.supports("\U0001d400")

    def test_unknown_private_use_excluded(self) -> None:
        assert not BUILTIN.supports("")


class TestProfiles:
    def test_four_profiles_exist(self) -> None:
        assert set(PROFILES) == {"builtin", "builtin+symbols", "builtin+symbols+cjk", "full"}

    def test_default_is_builtin(self) -> None:
        assert resolve_profile(None).name == "builtin"
        assert resolve_profile("").name == "builtin"

    def test_unknown_profile_lists_options(self) -> None:
        with pytest.raises(ValueError, match="builtin"):
            resolve_profile("crosspoint-x")

    def test_builtin_removes_box_drawing(self) -> None:
        cleaned, stats = filter_text("абв─где", "builtin")

        assert cleaned == "абвгде"
        assert stats.removed_clusters == 1

    def test_symbols_keeps_box_drawing(self) -> None:
        cleaned, stats = filter_text("абв─где", "builtin+symbols")

        assert cleaned == "абв─где"
        assert stats.removed_clusters == 0

    def test_symbols_keeps_math_alphabetic_but_builtin_does_not(self) -> None:
        assert BUILTIN_SYMBOLS.supports("\U0001d400") is False
        assert FULL.supports("\U0001d400") is True

    def test_full_keeps_everything(self) -> None:
        for char in ["─", "\U0001f300", "\U0001d400", "Ё", "、"]:
            assert FULL.supports(char), char

    def test_cjk_profile_keeps_japanese(self) -> None:
        cleaned, stats = filter_text("日本語とカタカナ", "builtin+symbols+cjk")

        assert cleaned == "日本語とカタカナ"
        assert stats.removed_clusters == 0

    def test_builtin_removes_cjk(self) -> None:
        cleaned, stats = filter_text("日本語", "builtin")

        assert cleaned == ""
        assert stats.removed_clusters == 3

    def test_profiles_are_monotonic(self) -> None:
        """Расширенный профиль всегда содержит базовый."""
        assert _count(BUILTIN) < _count(BUILTIN_SYMBOLS)
        assert _count(BUILTIN_SYMBOLS) < _count(BUILTIN_SYMBOLS_CJK)
        assert _count(BUILTIN_SYMBOLS_CJK) < _count(FULL)


def _count(profile) -> int:
    return sum(end - start + 1 for start, end in profile.ranges)


class TestGraphemeClusters:
    def test_simple_text_is_one_cluster_per_char(self) -> None:
        assert list(iter_clusters("абв")) == ["а", "б", "в"]

    def test_combining_mark_stays_with_base(self) -> None:
        assert list(iter_clusters("é")) == ["é"]

    def test_skin_tone_modifier_joins_emoji(self) -> None:
        thumb = "\U0001f44d\U0001f3fd"

        assert list(iter_clusters(thumb)) == [thumb]

    def test_zwj_sequence_is_one_cluster(self) -> None:
        family = "\U0001f468‍\U0001f469‍\U0001f467"

        assert list(iter_clusters(family)) == [family]

    def test_variation_selector_stays_with_heart(self) -> None:
        heart = "❤️"

        assert list(iter_clusters(heart)) == [heart]

    def test_regional_indicator_pair_is_one_cluster(self) -> None:
        flag = "\U0001f1f7\U0001f1fa"

        assert list(iter_clusters(flag)) == [flag]

    def test_three_code_point_emoji_is_removed_whole(self) -> None:
        emoji = "\U0001f44d\U0001f3fd️"
        assert len(emoji) == 3, "эмодзи из трёх кодовых точек"

        cleaned, stats = filter_text(f"а{emoji}б", "builtin")

        assert cleaned == "аб"
        assert stats.removed_characters == 3
        assert stats.removed_clusters == 1
        assert "‍" not in cleaned
        assert "\U0001f3fd" not in cleaned
        assert "️" not in cleaned

    def test_zwj_family_is_removed_without_orphans(self) -> None:
        family = "\U0001f468‍\U0001f469‍\U0001f467"

        cleaned, stats = filter_text(f"начало {family} конец", "builtin")

        assert cleaned == "начало  конец"
        assert "‍" not in cleaned
        assert stats.removed_clusters == 1

    def test_removed_count_is_in_characters_not_clusters(self) -> None:
        cleaned, stats = filter_text("\U0001f300\U0001f301 ok", "builtin")

        assert cleaned == " ok"
        assert stats.removed_clusters == 2
        assert stats.removed_characters == 2


class TestDocumentFiltering:
    def _doc(self, *nodes: dict) -> dict:
        return {"type": "doc", "content": list(nodes)}

    def test_text_is_filtered(self) -> None:
        document = self._doc(
            {"type": "paragraph", "content": [{"type": "text", "text": "до─после"}]}
        )

        result = filter_document(document, "builtin")

        assert result.document["content"][0]["content"][0]["text"] == "допосле"

    def test_heading_is_filtered_too(self) -> None:
        document = self._doc(
            {
                "type": "heading",
                "attrs": {"level": 2},
                "content": [{"type": "text", "text": "Глава ─ одна"}],
            }
        )

        result = filter_document(document, "builtin")

        assert "─" not in json.dumps(result.document, ensure_ascii=False)

    def test_nested_nodes_are_filtered(self) -> None:
        document = self._doc(
            {
                "type": "blockquote",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "цитата ─ тут"}],
                    }
                ],
            }
        )

        result = filter_document(document, "builtin")

        assert result.stats.removed_clusters == 1

    def test_image_description_is_filtered(self) -> None:
        document = self._doc(
            {"type": "image", "attrs": {"images": [{"image": "a"}], "description": "схема ─"}}
        )

        result = filter_document(document, "builtin")

        assert result.document["content"][0]["attrs"]["description"] == "схема "

    def test_clean_document_is_untouched(self) -> None:
        document = self._doc({"type": "paragraph", "content": [{"type": "text", "text": "чисто"}]})
        before = json.dumps(document, ensure_ascii=False, sort_keys=True)

        result = filter_document(document, "builtin")

        assert json.dumps(result.document, ensure_ascii=False, sort_keys=True) == before
        assert result.stats.touched is False

    def test_marks_survive_filtering(self) -> None:
        document = self._doc(
            {
                "type": "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "важный─",
                        "marks": [{"type": "bold"}],
                    }
                ],
            }
        )

        result = filter_document(document, "builtin")
        html = convert_document(result.document).html

        assert html == "<p><strong>важный</strong></p>"

    def test_wrapped_document_is_filtered(self) -> None:
        inner = self._doc({"type": "paragraph", "content": [{"type": "text", "text": "x─y"}]})
        wrapped = {"content": inner}

        result = filter_document(wrapped, "builtin")

        assert result.stats.removed_clusters == 1

    def test_empty_document(self) -> None:
        result = filter_document(None, "builtin")

        assert result.document == {}
        assert result.stats.touched is False


class TestReportAccounting:
    def test_counts_characters_and_chapters(self) -> None:
        report = Report()

        for _ in range(3):
            stats = filter_document(
                {
                    "type": "doc",
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "─" * 5}]}
                    ],
                },
                "builtin",
            ).stats
            apply_to_report(report, stats)

        assert report.removed_characters == 15
        assert report.chapters_with_removed_characters == 3

    def test_case_154_characters_in_3_chapters(self) -> None:
        report = Report()
        chunks = [50, 52, 52]

        for size in chunks:
            document = {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "a" + "─" * size}],
                    }
                ],
            }
            apply_to_report(report, filter_document(document, "builtin").stats)

        assert report.removed_characters == 154
        assert report.chapters_with_removed_characters == 3

    def test_no_counter_when_nothing_removed(self) -> None:
        report = Report()

        apply_to_report(report, filter_document(None, "builtin").stats)

        assert report.removed_characters == 0
        assert report.chapters_with_removed_characters == 0
        assert report.partial is False


class TestNoNamedEntities:
    @pytest.mark.parametrize("char", ["—", "«", "…", "′", "§"])
    def test_punctuation_kept_as_utf8(self, char: str) -> None:
        cleaned, stats = filter_text(f"текст{char}текст", "builtin")

        assert cleaned == f"текст{char}текст"
        assert stats.removed_clusters == 0

    def test_generated_xhtml_has_no_named_entities(self) -> None:
        for name in ["chapter.json", "chapter_rich.json", "chapter_marks.json"]:
            payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            chapter = _chapter(payload)
            result = filter_document(chapter["content"], "builtin")
            html = convert_document(result.document).html

            assert "&mdash;" not in html
            assert "&laquo;" not in html
            assert "&nbsp;" not in html
            for entity in _named_entities(html):
                assert entity in {"amp", "lt", "gt", "quot", "apos"}, entity

    def test_all_fixtures_stay_well_formed_after_filtering(self) -> None:
        checked = 0
        for path in sorted(FIXTURES.glob("chapter*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for document in _documents(payload):
                result = filter_document(document, "builtin")
                assert_well_formed(convert_document(result.document).html)
                checked += 1

        assert checked > 0

    def test_cyrillic_is_not_escaped(self) -> None:
        html = convert_document(
            {
                "type": "doc",
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Привет"}]}],
            }
        ).html

        assert "Привет" in html
        ET.fromstring(f'<body xmlns="http://www.w3.org/1999/xhtml">{html}</body>')


def _named_entities(html: str) -> set[str]:
    import re

    return set(re.findall(r"&([a-zA-Z]+);", html))


def _chapter(payload: dict) -> dict:
    data = payload["data"]
    return data if isinstance(data, dict) else data[0]


def _documents(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, list):
        return [item["content"] for item in data if isinstance(item.get("content"), dict)]
    content = data.get("content") if isinstance(data, dict) else None
    return [content] if isinstance(content, dict) else []
