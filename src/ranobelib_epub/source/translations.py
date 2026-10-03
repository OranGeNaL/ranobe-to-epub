"""Выбор ветки перевода и расчёт покрытия (решение 7, задачи 5.1-5.3).

Уникальность задачи: у главы до трёх веток, а выбранный перевод может покрывать лишь
часть книги. Молчаливый выбор дал бы книгу, часть глав которой переведена другим
переводчиком, поэтому выбор двухчастный — ветка по умолчанию плюс явный расчёт покрытия.

Установлено по фикстуре книги `94231--...`: ветка `branches[0]` — актуальная (её `id`
совпадает с `id` главы для всех 735 глав), ветка `branch_id=18996` покрывает все 735
глав, `18997` — 25, `20944` — 8. Ни одна команда не покрывает книгу целиком: основной
поток переводили последовательно `Re:Zero | Элиор` (436) и `UNDEAD CULT` (279), что для
длинной книги нормально и учтено расчётом покрытия.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Chapter, TranslationBranch

#: Команда не выбрана явно — берётся актуальная ветка каждой главы (решение 7).
DEFAULT_TRANSLATION: str | None = None


def default_branch(chapter: Chapter) -> TranslationBranch | None:
    """Актуальная ветка — первая в списке; None, если веток нет."""
    return chapter.default_branch


def find_branch_by_team(chapter: Chapter, team: str) -> TranslationBranch | None:
    """Ветка, в которой участвует команда `team` (сравнение без учёта регистра)."""
    wanted = team.casefold()
    for branch in chapter.branches:
        for name in branch.teams:
            if name.casefold() == wanted:
                return branch
    return None


def select_branch(chapter: Chapter, team: str | None = None) -> TranslationBranch | None:
    """Ветка для скачивания главы.

    С командой — ветка этой команды, иначе актуальная ветка по умолчанию. Глава без
    веток возвращает None: её помечаем недоступной (сценарий «Глава не имеет ни одной
    ветки перевода»).
    """
    if team:
        branch = find_branch_by_team(chapter, team)
        if branch is not None:
            return branch
    return default_branch(chapter)


@dataclass(frozen=True, slots=True)
class Coverage:
    """Покрытие выбранного перевода по книге."""

    total: int
    covered: int
    uncovered_labels: tuple[str, ...]
    team: str | None = None

    @property
    def is_partial(self) -> bool:
        return self.covered < self.total

    def summary(self) -> str:
        base = f"{self.covered} из {self.total}"
        if self.team:
            return f"перевод «{self.team}»: {base} глав"
        return f"актуальная ветка: {base} глав"


def compute_coverage(chapters: list[Chapter], team: str | None = None) -> Coverage:
    """Сколько глав покрывает выбранный перевод и какие номера остались без него.

    Без команды покрытие полное по построению: каждой главе подставляется её
    актуальная ветка. С командой покрытие показывает, сколько глав эта команда вела
    сама, а остальные главы перечисляются как непокрытые — они будут скачаны в
    актуальной ветке, но не этой командой.
    """
    if not team:
        return Coverage(total=len(chapters), covered=len(chapters), uncovered_labels=())

    uncovered: list[str] = []
    covered = 0
    for chapter in chapters:
        if find_branch_by_team(chapter, team) is not None:
            covered += 1
        else:
            uncovered.append(chapter.label or f"{chapter.volume}.{chapter.number}")

    return Coverage(
        total=len(chapters),
        covered=covered,
        uncovered_labels=tuple(uncovered),
        team=team,
    )


@dataclass(frozen=True, slots=True)
class TeamCoverage:
    """Перевод, доступный в книге, с числом покрываемых глав."""

    name: str
    covered: int
    total: int

    @property
    def is_default(self) -> bool:
        return False


def available_teams(chapters: list[Chapter]) -> tuple[TeamCoverage, ...]:
    """Список переводов книги, отсортированный по убыванию покрытия.

    Используется экраном выбора перевода: сначала идёт покрывающий больше всего глав,
    поэтому «актуальный» вариант оказывается первым без ручного сравнения.
    """
    total = len(chapters)
    counts: dict[str, int] = {}
    for chapter in chapters:
        seen: set[str] = set()
        for branch in chapter.branches:
            for name in branch.teams:
                key = name.casefold()
                if key not in seen:
                    seen.add(key)
                    counts[name] = counts.get(name, 0) + 1

    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    return tuple(TeamCoverage(name=name, covered=covered, total=total) for name, covered in ordered)


def apply_selection(chapters: list[Chapter], team: str | None = None) -> list[Chapter]:
    """Проставляет каждой главе `branch_id` выбранной ветки.

    Главы без веток остаются с `branch_id = None`: сборщик помечает их недоступными,
    не прерывая загрузку остальных.
    """
    for chapter in chapters:
        branch = select_branch(chapter, team)
        chapter.branch_id = branch.id if branch else None
    return chapters