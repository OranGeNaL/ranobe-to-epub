"""Фильтрация символов главы по профилю CrossPoint (задачи 9.5-9.6).

Фильтр применяется к TipTap-документу **до** конвертации в XHTML: тогда под правило
попадают и текст, и заголовки, и подписи к картинкам, а отчёт получает честные
счётчики. Править готовый HTML строками значило бы считать удалённое по вхождениям в
сериализованной разметке.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Report
from .graphemes import iter_clusters
from .profiles import DEFAULT_PROFILE, CharsetProfile, resolve_profile


@dataclass(slots=True)
class FilterStats:
    """Что удалил фильтр в одной главе."""

    removed_characters: int = 0
    removed_clusters: int = 0

    @property
    def touched(self) -> bool:
        return self.removed_clusters > 0


@dataclass(slots=True)
class FilterResult:
    """Отфильтрованный документ и статистика по главе."""

    document: dict
    stats: FilterStats = field(default_factory=FilterStats)


def filter_text(text: str, profile: CharsetProfile | str | None = None) -> tuple[str, FilterStats]:
    """Удаляет кластеры вне профиля, считая символы и кластеры.

    Профиль принимается именем или объектом: по умолчанию `builtin`.
    """
    resolved = profile if isinstance(profile, CharsetProfile) else resolve_profile(profile)
    kept: list[str] = []
    stats = FilterStats()

    for cluster in iter_clusters(text):
        if resolved.supports_cluster(cluster):
            kept.append(cluster)
            continue
        stats.removed_clusters += 1
        stats.removed_characters += len(cluster)

    return "".join(kept), stats


def _merge(target: FilterStats, source: FilterStats) -> None:
    target.removed_characters += source.removed_characters
    target.removed_clusters += source.removed_clusters


def filter_node(node: dict, profile: CharsetProfile, stats: FilterStats) -> dict:
    """Фильтрует текст узла и его атрибутов на месте (9.5)."""
    kind = node.get("type")

    if kind == "text":
        cleaned, node_stats = filter_text(node.get("text") or "", profile)
        if node_stats.touched:
            node["text"] = cleaned
            _merge(stats, node_stats)

    attrs = node.get("attrs")
    if isinstance(attrs, dict):
        for name in ("description", "alt", "title", "language"):
            value = attrs.get(name)
            if isinstance(value, str) and value:
                cleaned, node_stats = filter_text(value, profile)
                if node_stats.touched:
                    attrs[name] = cleaned
                    _merge(stats, node_stats)

    for child in node.get("content") or []:
        if isinstance(child, dict):
            filter_node(child, profile, stats)

    return node


def filter_document(
    document: dict | None, profile: CharsetProfile | str | None = DEFAULT_PROFILE
) -> FilterResult:
    """Фильтрует целый документ и возвращает его вместе со статистикой."""
    resolved = profile if isinstance(profile, CharsetProfile) else resolve_profile(profile)
    if not document:
        return FilterResult(document={}, stats=FilterStats())

    node = document
    if node.get("type") != "doc" and isinstance(node.get("content"), dict):
        node = node["content"]

    stats = FilterStats()
    filter_node(node, resolved, stats)
    return FilterResult(document=node, stats=stats)


def apply_to_report(report: Report, stats: FilterStats) -> None:
    """Заносит статистику главы в отчёт (задача 9.6)."""
    if not stats.touched:
        return
    report.removed_characters += stats.removed_characters
    report.chapters_with_removed_characters += 1