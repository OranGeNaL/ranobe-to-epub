"""Отчёт о проблемах сборки (решение 10, задачи 11.1, 11.3, 11.4).

Отчёт накапливается по ходу сборки и переживает её: главы, которые не удалось получить,
не прерывают работу, но обязаны быть видны с причиной. Пустой отчёт при полной
успешной сборке — тоже результат, а не отсутствие результата, поэтому итоговый размер
файла записывается всегда.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..charset.filter import FilterStats
from ..models import Chapter, MissingImage, Report, UnavailableChapter

#: События, которые нельзя пропустить молча (11.4).
MANDATORY_NOTES = ("18+", "символ")


class ReportRecorder:
    """Собирает отчёт и печатает его в текстовом режиме (11.1, 11.3)."""

    def __init__(self, total_chapters: int = 0) -> None:
        self.report = Report(total_chapters=total_chapters)

    def chapter_built(self) -> None:
        self.report.built_chapters += 1

    def chapter_unavailable(self, chapter: Chapter, reason: str) -> None:
        """Глава не получена: сборка продолжается, причина попадает в отчёт."""
        self.report.unavailable.append(
            UnavailableChapter(
                label=chapter.label or f"{chapter.volume}.{chapter.number}",
                volume=chapter.volume,
                number=chapter.number,
                name=chapter.name,
                reason=reason,
            )
        )

    def image_missing(
        self,
        url: str | None,
        reason: str,
        chapter_label: str = "",
        reference: str = "",
    ) -> None:
        self.report.missing_images.append(
            MissingImage(
                chapter_label=chapter_label,
                reference=reference,
                url=url,
                reason=reason,
            )
        )

    def unknown_node(self, description: str) -> None:
        self.report.unknown_nodes.append(description)

    def filtered(self, stats: FilterStats) -> None:
        if not stats.touched:
            return
        self.report.removed_characters += stats.removed_characters
        self.report.chapters_with_removed_characters += 1

    def age_confirmed(self, level: int | None, label: str, message: str) -> None:
        self.report.age_restriction_id = level
        self.report.age_restriction_label = label
        self.report.age_confirmed_chapters += 1
        self.report.notes.append(message)

    def warnings(self, messages: list[str]) -> None:
        self.report.unknown_nodes.extend(messages)

    def finished(self, path: str, size: int) -> None:
        self.report.output_path = path
        self.report.file_size = size

    @property
    def partial(self) -> bool:
        return self.report.partial

    def render(self) -> str:
        """Текстовый отчёт для неинтерактивного режима (13.3)."""
        report = self.report
        lines = [
            "Отчёт о сборке",
            f"  собрано глав: {report.built_chapters} из {report.total_chapters or '?'}",
            f"  недоступно глав: {len(report.unavailable)}",
            f"  пропущено изображений: {len(report.missing_images)}",
            f"  неизвестных узлов: {len(report.unknown_nodes)}",
            f"  удалено символов: {report.removed_characters} "
            f"в {report.chapters_with_removed_characters} главах",
        ]

        if report.age_confirmed:
            lines.append(
                f"  возрастное ограничение «{report.age_restriction_label}» "
                f"({report.age_restriction_id}) подтверждено автоматически"
            )

        if report.file_size is not None:
            lines.append(f"  размер файла: {_human_size(report.file_size)}")
        if report.output_path:
            lines.append(f"  файл: {report.output_path}")

        for item in report.unavailable:
            lines.append(f"  - {item.label} «{item.name}»: {item.reason}")
        for item in report.missing_images:
            lines.append(f"  - изображение {item.url or item.reference}: {item.reason}")

        return "\n".join(lines)


@dataclass(slots=True)
class DownloadOutcome:
    """Результат попытки получить главу."""

    chapter: Chapter | None = None
    document: dict | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.chapter is not None


@dataclass(slots=True)
class Progress:
    """Снимок прогресса для TUI и stdout (12.4, 14.6)."""

    done: int = 0
    total: int = 0
    elapsed: float = 0.0
    current_label: str = ""
    images_done: int = 0
    images_total: int = 0
    _started: float = field(default=0.0)

    def snapshot(self) -> Progress:
        """Неизменяемая копия для подписчиков прогресса."""
        return Progress(
            done=self.done,
            total=self.total,
            elapsed=self.elapsed,
            current_label=self.current_label,
            images_done=self.images_done,
            images_total=self.images_total,
            _started=self._started,
        )

    @property
    def percent(self) -> float:
        return (self.done / self.total * 100) if self.total else 0.0

    @property
    def speed(self) -> float:
        """Глав в секунду."""
        return self.done / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def eta(self) -> float:
        """Оценка оставшегося времени в секундах."""
        remaining = max(0, self.total - self.done)
        return remaining / self.speed if self.speed > 0 else 0.0

    def render(self) -> str:
        """Строка прогресса: номер, всего, скорость и оценка."""
        return (
            f"{self.done}/{self.total} глав "
            f"({self.percent:.1f}%) · {self.speed:.2f} глав/с · "
            f"осталось {_human_duration(self.eta)}"
        )


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} Б"
        value /= 1024
    return f"{value:.1f} ГБ"


def _human_duration(seconds: float) -> str:
    if seconds <= 0:
        return "0 с"
    minutes, secs = divmod(round(seconds), 60)
    if minutes:
        return f"{minutes} мин {secs} с"
    return f"{secs} с"