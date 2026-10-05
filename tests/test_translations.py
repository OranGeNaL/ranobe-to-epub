"""Тесты выбора ветки перевода и покрытия (задачи 5.1-5.3)."""

from __future__ import annotations

from ranobelib_epub.models import Chapter, TranslationBranch
from ranobelib_epub.sources.ranobelib.parsing import parse_chapters
from ranobelib_epub.translations import (
    apply_selection,
    available_teams,
    compute_coverage,
    default_branch,
    find_branch_by_team,
    select_branch,
)


def chapter(chapter_id: int, branches: tuple[TranslationBranch, ...]) -> Chapter:
    return Chapter(id=chapter_id, volume=1, number=str(chapter_id), name="n", branches=branches)


DIFFERLEX = TranslationBranch(id=20944, name="differlex", teams=("DifferLex",))
MAIN = TranslationBranch(id=18996, name="main", teams=("Re:Zero | Элиор", "UNDEAD CULT"))
LOXOTRON = TranslationBranch(id=18997, name="loxotron", teams=("Loxotron's Translations",))


class TestDefaultBranch:
    def test_first_branch_is_selected_by_default(self) -> None:
        subject = chapter(1, (DIFFERLEX, MAIN, LOXOTRON))

        assert default_branch(subject) is DIFFERLEX
        assert select_branch(subject) is DIFFERLEX

    def test_three_branches_default_is_first(self) -> None:
        subject = chapter(1, (DIFFERLEX, MAIN, LOXOTRON))

        assert select_branch(subject).id == 20944

    def test_chapter_without_branches(self) -> None:
        subject = chapter(1, ())

        assert default_branch(subject) is None
        assert select_branch(subject) is None

    def test_no_branches_marks_chapter_unavailable(self) -> None:
        subject = chapter(1, ())

        apply_selection([subject])

        assert subject.branch_id is None


class TestTeamSelection:
    def test_team_branch_is_used(self) -> None:
        subject = chapter(1, (DIFFERLEX, MAIN, LOXOTRON))

        assert select_branch(subject, "Loxotron's Translations") is LOXOTRON

    def test_team_match_is_case_insensitive(self) -> None:
        subject = chapter(1, (DIFFERLEX, MAIN, LOXOTRON))

        assert select_branch(subject, "loxotron's translations") is LOXOTRON

    def test_falls_back_to_default_for_other_chapters(self) -> None:
        subject = chapter(1, (MAIN,))

        assert select_branch(subject, "Loxotron's Translations") is MAIN

    def test_team_found_in_second_position(self) -> None:
        subject = chapter(1, (DIFFERLEX, LOXOTRON))

        assert find_branch_by_team(subject, "Loxotron's Translations") is LOXOTRON

    def test_unknown_team_returns_none(self) -> None:
        subject = chapter(1, (MAIN,))

        assert find_branch_by_team(subject, "Нет Такой") is None


class TestCoverage:
    def test_full_coverage_without_team(self) -> None:
        chapters = [chapter(i, (MAIN,)) for i in range(5)]

        coverage = compute_coverage(chapters)

        assert (coverage.covered, coverage.total) == (5, 5)
        assert coverage.is_partial is False
        assert coverage.uncovered_labels == ()

    def test_partial_coverage(self) -> None:
        chapters = [chapter(1, (LOXOTRON,)), chapter(2, (MAIN,)), chapter(3, (MAIN,))]
        apply_selection(chapters)

        coverage = compute_coverage(chapters, "Loxotron's Translations")

        assert (coverage.covered, coverage.total) == (1, 3)
        assert coverage.is_partial is True
        assert coverage.uncovered_labels == ("1.2", "1.3")

    def test_coverage_summary_mentions_team(self) -> None:
        chapters = [chapter(1, (LOXOTRON,))]
        apply_selection(chapters)
        coverage = compute_coverage(chapters, "Loxotron's Translations")

        assert "Loxotron's Translations" in coverage.summary()
        assert "1 из 1" in coverage.summary()

    def test_team_name_must_match_exactly(self) -> None:
        """Сравнение имени без учёта регистра, но без подстрочного поиска: неоднозначные
        имена вроде `re:Translators` и `Re:Zero | Элиор` дают ложные совпадения."""
        chapters = [chapter(1, (LOXOTRON,))]

        assert compute_coverage(chapters, "Loxotron's").covered == 0

    def test_apply_selection_sets_branch_ids(self) -> None:
        chapters = [chapter(1, (DIFFERLEX, LOXOTRON)), chapter(2, (MAIN,))]

        apply_selection(chapters, "Loxotron's Translations")

        assert chapters[0].branch_id == 18997
        assert chapters[1].branch_id == 18996


class TestAvailableTeams:
    def test_teams_sorted_by_coverage(self) -> None:
        chapters = [chapter(i, (MAIN,)) for i in range(10)]
        chapters.append(chapter(11, (DIFFERLEX, LOXOTRON)))

        teams = available_teams(chapters)

        assert [t.name for t in teams] == [
            "Re:Zero | Элиор",
            "UNDEAD CULT",
            "DifferLex",
            "Loxotron's Translations",
        ]
        assert teams[0].covered == 10
        assert teams[0].total == 11

    def test_chapter_counts_once_per_team(self) -> None:
        chapters = [chapter(1, (MAIN,))]

        teams = available_teams(chapters)

        assert {t.name: t.covered for t in teams} == {"Re:Zero | Элиор": 1, "UNDEAD CULT": 1}

    def test_no_branches_means_no_teams(self) -> None:
        assert available_teams([chapter(1, ())]) == ()


class TestRealBookFixture:
    def test_main_branch_covers_every_chapter(self) -> None:
        import json
        from pathlib import Path

        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "chapters.json").read_text(encoding="utf-8")
        )
        chapters = parse_chapters(payload)

        coverage = compute_coverage(chapters, "UNDEAD CULT")

        assert coverage.total == 735
        assert coverage.covered == 279
        assert coverage.is_partial is True
        assert len(coverage.uncovered_labels) == 735 - 279

    def test_default_selection_never_leaves_chapter_without_branch(self) -> None:
        import json
        from pathlib import Path

        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "chapters.json").read_text(encoding="utf-8")
        )
        chapters = apply_selection(parse_chapters(payload))

        assert all(c.branch_id is not None for c in chapters)
        assert len({c.branch_id for c in chapters}) == 3, "использованы все три ветки книги"
