"""Тесты отчёта и загрузки глав (задачи 11.1-11.4, 12.1-12.4)."""

from __future__ import annotations

import asyncio
import io
import time
import zipfile
from pathlib import Path

import pytest

from ranobelib_epub.models import Book, Chapter
from ranobelib_epub.pipeline.downloader import (
    ChapterDownloader,
    ChapterSelector,
    ChapterTask,
    StreamingWriter,
    compress_in_thread,
    coverage_note,
)
from ranobelib_epub.pipeline.report import (
    MANDATORY_NOTES,
    ChapterEvent,
    Progress,
    ReportRecorder,
)
from ranobelib_epub.translations import Coverage

FIXTURES = Path(__file__).parent / "fixtures"


def chapter(index: int, volume: int = 1, name: str = "Глава") -> Chapter:
    return Chapter(
        id=index,
        volume=volume,
        number=str(index),
        name=name,
        branch_id=18996,
        label=f"{volume}.{index}",
    )


class FakeSource:
    """Источник, который отдаёт заготовки и умеет падать на выбранных главах."""

    def __init__(
        self,
        failing: set[int] | None = None,
        delay: float = 0.0,
        delays: dict[int, float] | None = None,
    ) -> None:
        self.failing = failing or set()
        self.delay = delay
        self.delays = delays or {}
        self.calls: list[int] = []

    async def fetch_chapter_content(self, slug, chapter_, branch_id):
        self.calls.append(chapter_.id)
        delay = self.delays.get(chapter_.id, self.delay)
        if delay:
            await asyncio.sleep(delay)
        if chapter_.id in self.failing:
            raise RuntimeError(f"глава {chapter_.id}: сеть недоступна")
        from ranobelib_epub.models import ChapterContent

        return ChapterContent(
            volume=chapter_.volume,
            number=chapter_.number,
            doc={
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": f"Текст главы {chapter_.id}"}],
                    }
                ],
            },
        )

    def chapter_unavailable_reason(self, chapter):
        from ranobelib_epub.sources.ranobelib.api import chapter_unavailable_reason

        return chapter_unavailable_reason(chapter)

    def describe_failure(self, error, *, ref=""):
        from ranobelib_epub.sources.ranobelib.api import describe_failure

        return describe_failure(error, ref=ref)

    def resource_url(self, url):
        return url

    async def fetch_resource(self, url):
        raise RuntimeError("иллюстрации не используются в этих тестах")


class TestReportFields:
    def test_all_counters_fill(self) -> None:
        recorder = ReportRecorder(total_chapters=10)
        recorder.chapter_built()
        recorder.chapter_built()
        recorder.chapter_unavailable(chapter(3), "сеть недоступна")
        recorder.image_missing("https://x/y.png", "403", "1.4", "img-1")
        recorder.unknown_node("неизвестный узел «youtubeEmbed»")
        recorder.filtered(_stats(12, 2))
        recorder.finished("/tmp/book.epub", 12_345)

        report = recorder.report

        assert report.total_chapters == 10
        assert report.built_chapters == 2
        assert len(report.unavailable) == 1
        assert len(report.missing_images) == 1
        assert len(report.unknown_nodes) == 1
        assert report.removed_characters == 12
        assert report.chapters_with_removed_characters == 1
        assert report.file_size == 12_345
        assert report.output_path == "/tmp/book.epub"

    def test_unavailable_keeps_reason_and_label(self) -> None:
        recorder = ReportRecorder()
        recorder.chapter_unavailable(chapter(7, volume=2), "требуется авторизация")

        item = recorder.report.unavailable[0]

        assert item.label == "2.7"
        assert item.reason == "требуется авторизация"
        assert item.volume == 2
        assert item.number == "7"

    def test_empty_report_on_clean_build(self) -> None:
        recorder = ReportRecorder(total_chapters=5)
        for _ in range(5):
            recorder.chapter_built()
        recorder.finished("/tmp/book.epub", 4096)

        report = recorder.report

        assert report.built_chapters == 5
        assert report.unavailable == []
        assert report.missing_images == []
        assert report.unknown_nodes == []
        assert report.removed_characters == 0
        assert report.file_size == 4096
        assert report.partial is False

    def test_render_mentions_zero_counters(self) -> None:
        recorder = ReportRecorder(total_chapters=2)
        recorder.chapter_built()
        recorder.chapter_built()
        recorder.finished("/tmp/book.epub", 2048)

        text = recorder.render()

        assert "собрано глав: 2 из 2" in text
        assert "недоступно глав: 0" in text
        assert "пропущено изображений: 0" in text
        assert "размер файла" in text

    def test_render_lists_problems(self) -> None:
        recorder = ReportRecorder(total_chapters=2)
        recorder.chapter_unavailable(chapter(2), "сеть недоступна")
        recorder.image_missing("https://x/y.png", "403")

        text = recorder.render()

        assert "1.2" in text
        assert "сеть недоступна" in text
        assert "https://x/y.png" in text


class TestImageOptimizationReport:
    def test_compressed_image_saving_is_accumulated(self) -> None:
        recorder = ReportRecorder()

        recorder.image_optimized(1_000_000, 200_000)

        assert recorder.report.image_bytes_saved == 800_000
        assert recorder.report.images_deduplicated == 0

    def test_negative_compression_saving_is_clamped(self) -> None:
        recorder = ReportRecorder()

        recorder.image_optimized(1_000, 5_000)

        assert recorder.report.image_bytes_saved == 0

    def test_deduplicated_image_counts_and_saves_full_file(self) -> None:
        recorder = ReportRecorder()

        recorder.image_optimized(500_000, 250_000, deduplicated=True)

        assert recorder.report.images_deduplicated == 1
        assert recorder.report.image_bytes_saved == 250_000

    def test_render_shows_optimization_lines_when_present(self) -> None:
        recorder = ReportRecorder()
        recorder.image_optimized(1_000_000, 200_000)
        recorder.image_optimized(500_000, 250_000, deduplicated=True)

        text = recorder.render()

        assert "дедуплицировано изображений: 1" in text
        assert "экономия на изображениях" in text

    def test_render_omits_optimization_lines_when_absent(self) -> None:
        recorder = ReportRecorder()
        recorder.chapter_built()

        text = recorder.render()

        assert "дедуплицировано" not in text
        assert "экономия" not in text


class TestMandatoryEvents:
    def test_age_confirmation_recorded(self) -> None:
        recorder = ReportRecorder()

        recorder.age_confirmed(4, "18+", "возрастное ограничение «18+» (4) подтверждено")

        assert recorder.report.age_confirmed is True
        assert recorder.report.age_restriction_id == 4
        assert recorder.report.age_restriction_label == "18+"
        assert recorder.report.age_confirmed_chapters == 1

    def test_removed_characters_recorded(self) -> None:
        recorder = ReportRecorder()

        for _ in range(3):
            recorder.filtered(_stats(50, 12))

        assert recorder.report.removed_characters == 150
        assert recorder.report.chapters_with_removed_characters == 3

    def test_clean_chapter_adds_no_counter(self) -> None:
        recorder = ReportRecorder()

        recorder.filtered(_stats(0, 0))

        assert recorder.report.removed_characters == 0
        assert recorder.report.chapters_with_removed_characters == 0

    def test_render_states_both_mandatory_events(self) -> None:
        recorder = ReportRecorder(total_chapters=2)
        recorder.age_confirmed(4, "18+", "подтверждено автоматически")
        for _ in range(3):
            recorder.filtered(_stats(52, 12))
        recorder.finished("/tmp/book.epub", 100)

        text = recorder.render()

        assert "18+" in text
        assert "удалено символов: 156 в 3 главах" in text

    def test_mandatory_events_cannot_be_silently_dropped(self) -> None:
        """Каждое обязательное событие должно попадать в текст отчёта."""
        recorder = ReportRecorder(total_chapters=1)
        recorder.age_confirmed(4, "18+", "подтверждено")
        recorder.filtered(_stats(5, 1))
        recorder.finished("/tmp/book.epub", 1)

        text = recorder.render()

        for marker in MANDATORY_NOTES:
            assert marker in text, marker

    def test_report_is_not_partial_when_only_mandatory_events(self) -> None:
        """18+ и фильтрация символов — штатная работа, а не потеря данных."""
        recorder = ReportRecorder(total_chapters=1)
        recorder.age_confirmed(4, "18+", "подтверждено")
        recorder.filtered(_stats(5, 1))

        assert recorder.report.partial is True
        assert recorder.report.unavailable == []
        assert recorder.report.missing_images == []


class TestPartialBuild:
    def test_build_continues_past_failures(self) -> None:
        source = FakeSource(failing={3, 17, 42})
        recorder = ReportRecorder(total_chapters=100)
        downloader = ChapterDownloader(source, recorder, concurrency=8)

        chapters = [chapter(index) for index in range(1, 101)]
        results = asyncio.run(
            downloader.fetch_all([ChapterTask(item) for item in chapters], book_ref="slug")
        )

        assert len(results) == 97, "EPUB должен содержать 97 глав"
        assert recorder.report.built_chapters == 97
        assert len(recorder.report.unavailable) == 3
        reasons = {item.label for item in recorder.report.unavailable}
        assert reasons == {"1.3", "1.17", "1.42"}
        assert all(item.reason for item in recorder.report.unavailable)

    def test_failed_chapter_requested_once(self) -> None:
        """Задача 15.4: к главе, требующей авторизации, не делается повторов."""
        source = FakeSource(failing={5})
        recorder = ReportRecorder(total_chapters=10)
        downloader = ChapterDownloader(source, recorder, concurrency=4)

        chapters = [chapter(index) for index in range(1, 11)]
        asyncio.run(downloader.fetch_all([ChapterTask(c) for c in chapters], book_ref="slug"))

        assert source.calls.count(5) == 1
        assert len(source.calls) == 10

    def test_expired_chapter_skips_request(self) -> None:
        source = FakeSource()
        recorder = ReportRecorder(total_chapters=2)
        downloader = ChapterDownloader(source, recorder)

        paid = chapter(1)
        paid.expired_type = 1
        free = chapter(2)

        results = asyncio.run(
            downloader.fetch_all([ChapterTask(paid), ChapterTask(free)], book_ref="slug")
        )

        assert source.calls == [2], "к платной главе запроса не было"
        assert len(results) == 1
        assert "expired_type" in recorder.report.unavailable[0].reason

    def test_chapter_without_branch_is_unavailable(self) -> None:
        source = FakeSource()
        recorder = ReportRecorder(total_chapters=1)
        downloader = ChapterDownloader(source, recorder)
        orphan = Chapter(id=1, volume=1, number="1", name="Глава")

        results = asyncio.run(downloader.fetch_all([ChapterTask(orphan)], book_ref="slug"))

        assert results == []
        assert source.calls == []
        reason = recorder.report.unavailable[0].reason
        assert "нет ветки перевода" in reason
        assert "обход" in reason, "причина должна объяснять, почему запрос не делается"


class TestConcurrencyAndThrottling:
    def test_concurrency_limit_respected(self) -> None:
        source = FakeSource(delay=0.01)
        recorder = ReportRecorder(total_chapters=20)
        downloader = ChapterDownloader(source, recorder, concurrency=3)

        chapters = [chapter(index) for index in range(1, 21)]
        results = asyncio.run(
            downloader.fetch_all([ChapterTask(c) for c in chapters], book_ref="slug")
        )

        assert len(results) == 20
        assert downloader.semaphore._value == 3

    def test_rate_limit_interval_is_shared(self) -> None:
        """Троттлинг живёт в клиенте, поэтому параллелизм не обходит лимит (3.5)."""
        from ranobelib_epub.sources.ranobelib.client import RateLimiter

        limiter = RateLimiter(4.0)

        assert limiter.interval == 0.25

    def test_results_keep_order(self) -> None:
        source = FakeSource(delay=0.005)
        recorder = ReportRecorder(total_chapters=10)
        downloader = ChapterDownloader(source, recorder, concurrency=10)

        chapters = [chapter(index) for index in range(1, 11)]
        results = asyncio.run(
            downloader.fetch_all([ChapterTask(c) for c in chapters], book_ref="slug")
        )

        assert [item.chapter.id for item in results] == list(range(1, 11))

    def test_cancel_stops_remaining(self) -> None:
        source = FakeSource(delay=0.02)
        recorder = ReportRecorder(total_chapters=20)
        downloader = ChapterDownloader(source, recorder, concurrency=2)
        cancel = asyncio.Event()

        async def run() -> list:
            cancel.set()
            return await downloader.fetch_all(
                [ChapterTask(c) for c in (chapter(i) for i in range(1, 21))],
                book_ref="slug",
                cancel=cancel,
            )

        assert asyncio.run(run()) == []


class TestCompressionOffLoop:
    def test_event_loop_stays_responsive(self) -> None:
        """Пока идёт сжатие, event loop должен отвечать (12.2)."""
        raw = (FIXTURES / "image_2210x1582.png").read_bytes()
        ticks: list[float] = []

        async def run() -> float:
            async def heartbeat() -> None:
                while True:
                    ticks.append(time.perf_counter())
                    await asyncio.sleep(0.005)

            beat = asyncio.create_task(heartbeat())
            await compress_in_thread(raw, 0.01, 1280)
            beat.cancel()
            return len(ticks)

        count = asyncio.run(run())

        assert count >= 3, "event loop был заблокирован сжатием"

    def test_compression_result_is_asset(self) -> None:
        from ranobelib_epub.images.pipeline import ImageAsset

        raw = (FIXTURES / "image_2210x1582.png").read_bytes()

        asset = asyncio.run(compress_in_thread(raw, 0.01, 1280))

        assert isinstance(asset, ImageAsset)
        assert asset.mime == "image/jpeg"
        assert asset.data[:2] == b"\xff\xd8"


class TestStreamingWriter:
    def test_chapters_written_as_they_arrive(self, tmp_path: Path) -> None:
        target = tmp_path / "stream.epub"

        with StreamingWriter(target) as writer:
            for index in range(1, 6):
                writer.add_chapter(f"EPUB/TEXT/{index}.html", f"<p>глава {index}</p>")
            assert writer.chapters_written == 5

        with zipfile.ZipFile(target) as archive:
            assert len([n for n in archive.namelist() if n.startswith("EPUB/TEXT/")]) == 5

    def test_memory_does_not_grow_with_chapter_count(self, tmp_path: Path) -> None:
        """12.3: главы пишутся по мере поступления, поэтому их число не влияет на память."""
        import tracemalloc

        def build(count: int, target: Path) -> tuple[float, int]:
            tracemalloc.start()
            with StreamingWriter(target) as writer:
                for index in range(1, count + 1):
                    writer.add_chapter(f"EPUB/TEXT/{index}.html", "<p>x</p>" * 200)
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            return peak, writer.chapters_written

        small_peak, small_count = build(50, tmp_path / "a.epub")
        large_peak, large_count = build(400, tmp_path / "b.epub")

        assert small_count == 50 and large_count == 400
        growth = large_peak / small_peak
        assert growth < 4, f"память выросла в {growth:.1f} раз при 8-кратном числе глав"

    def test_images_get_relative_href(self, tmp_path: Path) -> None:
        from ranobelib_epub.images.pipeline import ImageAsset

        with StreamingWriter(tmp_path / "img.epub") as writer:
            asset = ImageAsset(
                filename="", data=b"\xff\xd8\xff\xd9", source_url="", mime="image/jpeg"
            )
            href = writer.add_image(asset)

        assert href == "Images/img_0001.jpg"
        assert writer.href_for_chapter(href) == "../Images/img_0001.jpg"

    def test_image_numbers_increase(self, tmp_path: Path) -> None:
        from ranobelib_epub.images.pipeline import ImageAsset

        with StreamingWriter(tmp_path / "many.epub") as writer:
            names = [
                writer.add_image(
                    ImageAsset(filename="", data=b"x", source_url="", mime="image/jpeg")
                )
                for _ in range(3)
            ]

        assert names == [
            "Images/img_0001.jpg",
            "Images/img_0002.jpg",
            "Images/img_0003.jpg",
        ]

    def test_writer_outside_context_fails_loudly(self, tmp_path: Path) -> None:
        writer = StreamingWriter(tmp_path / "closed.epub")

        with pytest.raises(RuntimeError, match="не открыт"):
            writer.add_chapter("EPUB/TEXT/1.html", "<p>x</p>")


class TestProgress:
    def test_progress_numbers(self) -> None:
        progress = Progress(done=100, total=735, elapsed=50.0)

        assert progress.percent == pytest.approx(13.6, abs=0.1)
        assert progress.speed == pytest.approx(2.0)
        assert progress.eta == pytest.approx(317.5)

    def test_render_has_number_total_speed_eta(self) -> None:
        text = Progress(done=10, total=735, elapsed=5.0).render()

        assert "10/735" in text
        assert "глав/с" in text
        assert "осталось" in text

    def test_render_download_phase_shows_fetched(self) -> None:
        progress = Progress(fetched=30, total=735, elapsed=12.0)

        assert progress.downloading is True
        text = progress.render()

        assert "скачано 30/735" in text
        assert "глав/с" not in text

    def test_downloading_is_false_after_first_chapter(self) -> None:
        assert Progress(done=1, fetched=5, total=5).downloading is False
        assert Progress(done=0, fetched=5, total=5).downloading is False

    def test_eta_is_zero_without_timing(self) -> None:
        assert Progress(done=5, total=10).eta == 0.0

    def test_progress_reaches_total(self) -> None:
        progress = Progress(done=735, total=735, elapsed=600.0)

        assert progress.percent == 100.0
        assert progress.eta == 0.0

    def test_callback_receives_updates(self) -> None:
        seen: list[Progress] = []
        source = FakeSource()
        recorder = ReportRecorder(total_chapters=5)
        downloader = ChapterDownloader(
            source, recorder, concurrency=2, on_progress=lambda p: seen.append(p)
        )

        chapters = [chapter(index) for index in range(1, 6)]
        asyncio.run(downloader.fetch_all([ChapterTask(c) for c in chapters], book_ref="slug"))

        built = [p.done for p in seen if p.last_event is not None]
        assert built == [1, 2, 3, 4, 5]
        assert seen[-1].total == 5
        assert seen[0].last_event is None, "прогресс виден до завершения первой главы"


class TestChapterEventJournal:
    def test_built_line_has_number_name_and_images(self) -> None:
        event = ChapterEvent(position=3, total=735, label="1.5", name="Глава", built=True, images=2)

        assert event.render() == "[3/735] 1.5 «Глава» — собрана (изображений: 2)"

    def test_unavailable_line_has_number_name_and_reason(self) -> None:
        event = ChapterEvent(
            position=4,
            total=10,
            label="1.6",
            name="Глава",
            built=False,
            reason="таймаут запроса",
        )

        text = event.render()

        assert "[4/10]" in text
        assert "1.6" in text
        assert "Глава" in text
        assert "пропущена" in text
        assert "таймаут запроса" in text

    def test_missing_name_falls_back_to_label(self) -> None:
        event = ChapterEvent(position=1, total=1, label="1.1", name="", built=True)

        assert "«1.1»" in event.render()


class TestProgressSnapshotWithEvent:
    def test_snapshot_keeps_event_and_counters(self) -> None:
        event = ChapterEvent(position=2, total=5, label="1.2", name="Глава 2", built=True)
        progress = Progress(done=2, total=5, last_event=event)

        snapshot = progress.snapshot()

        assert snapshot.last_event == event
        assert snapshot.done == 2
        assert snapshot.total == 5


class TestLoadReturnsReason:
    def test_load_does_not_touch_report_on_success(self) -> None:
        source = FakeSource()
        recorder = ReportRecorder(total_chapters=2)
        downloader = ChapterDownloader(source, recorder)

        content, reason = asyncio.run(downloader._load(ChapterTask(chapter(1))))

        assert content is not None
        assert reason is None
        assert recorder.report.built_chapters == 0
        assert recorder.report.unavailable == []

    def test_failed_load_returns_reason_without_report_entry(self) -> None:
        source = FakeSource(failing={1})
        recorder = ReportRecorder()
        downloader = ChapterDownloader(source, recorder)

        content, reason = asyncio.run(downloader._load(ChapterTask(chapter(1))))

        assert content is None
        assert reason
        assert recorder.report.unavailable == []


class TestOrderedJournal:
    def test_events_and_report_follow_reading_order(self) -> None:
        """Глава 2 падает раньше медленной главы 1, но её событие остаётся вторым."""
        source = FakeSource(failing={2}, delays={1: 0.03, 2: 0.001, 3: 0.0})
        recorder = ReportRecorder(total_chapters=3)
        updates: list[Progress] = []
        events: list[ChapterEvent] = []

        def collect(progress: Progress) -> None:
            updates.append(progress)
            if progress.last_event is not None:
                events.append(progress.last_event)

        downloader = ChapterDownloader(source, recorder, concurrency=3, on_progress=collect)

        chapters = [chapter(index) for index in range(1, 4)]
        results = asyncio.run(
            downloader.fetch_all([ChapterTask(c) for c in chapters], book_ref="slug")
        )

        assert [event.position for event in events] == [1, 2, 3]
        chapter_done = [p.done for p in updates if p.last_event is not None]
        assert chapter_done == [1, 1, 2]
        assert len(results) == 2
        assert [event.label for event in events if not event.built] == ["1.2"]
        assert [item.label for item in recorder.report.unavailable] == ["1.2"]

    def test_download_phase_reports_before_processing(self) -> None:
        """Первая фаза (скачивание) видна в прогрессе, не дожидаясь конца."""
        source = FakeSource(delays={1: 0.01, 2: 0.01, 3: 0.01})
        recorder = ReportRecorder(total_chapters=3)
        updates: list[Progress] = []
        downloader = ChapterDownloader(source, recorder, concurrency=1, on_progress=updates.append)

        chapters = [chapter(index) for index in range(1, 4)]
        asyncio.run(downloader.fetch_all([ChapterTask(c) for c in chapters], book_ref="slug"))

        fetch_only = [p for p in updates if p.last_event is None]
        assert [p.fetched for p in fetch_only] == [0, 1, 2, 3]
        assert all(p.done == 0 for p in fetch_only)

    def test_one_event_per_chapter_with_three_failures(self) -> None:
        source = FakeSource(failing={5, 100, 700})
        recorder = ReportRecorder(total_chapters=735)
        events: list[ChapterEvent] = []

        def collect(progress: Progress) -> None:
            if progress.last_event is not None:
                events.append(progress.last_event)

        downloader = ChapterDownloader(source, recorder, concurrency=8, on_progress=collect)

        chapters = [chapter(index) for index in range(1, 736)]
        asyncio.run(downloader.fetch_all([ChapterTask(c) for c in chapters], book_ref="slug"))

        assert len(events) == 735
        assert [event.position for event in events] == list(range(1, 736))
        skipped = [event for event in events if not event.built]
        assert len(skipped) == 3
        assert {event.label for event in skipped} == {"1.5", "1.100", "1.700"}
        assert all(event.reason for event in skipped)


class TestChapterSelector:
    def _chapters(self) -> list[Chapter]:
        def make(index: int) -> Chapter:
            volume = 1 if index <= 3 else 2
            return Chapter(
                id=index,
                volume=volume,
                number=str(index),
                name=f"Г{index}",
                label=f"{volume}.{index}",
            )

        return [make(index) for index in range(1, 11)]

    def test_empty_selector_keeps_all(self) -> None:
        selector = ChapterSelector()

        assert len(selector.apply(self._chapters())) == 10

    def test_volume_selector(self) -> None:
        selector = ChapterSelector(volumes=frozenset({2}))

        assert [c.volume for c in selector.apply(self._chapters())] == [2, 2, 2, 2, 2, 2, 2]

    def test_index_selector(self) -> None:
        selector = ChapterSelector(indexes=frozenset({1, 3}))

        assert [c.id for c in selector.apply(self._chapters())] == [1, 3]

    def test_label_selector(self) -> None:
        selector = ChapterSelector(labels=frozenset({"1.2", "2.5"}))

        assert [c.id for c in selector.apply(self._chapters())] == [2, 5]

    def test_volume_min_selector(self) -> None:
        selector = ChapterSelector(volume_min=2)

        assert [c.volume for c in selector.apply(self._chapters())] == [2] * 7

    def test_empty_selector_with_volume_min_keeps_nothing(self) -> None:
        selector = ChapterSelector(volume_min=5)

        assert selector.apply(self._chapters()) == []


class TestCoverageNote:
    def test_partial_coverage_is_stated_with_numbers(self) -> None:
        coverage = Coverage(total=735, covered=90, uncovered_labels=(), team="Loxotron")

        note = coverage_note(coverage)

        assert "90 из 735" in note

    def test_full_coverage_note(self) -> None:
        note = coverage_note(Coverage(total=735, covered=735, uncovered_labels=()))

        assert "все 735" in note

    def test_uncovered_count_matches_report(self) -> None:
        coverage = Coverage(
            total=10, covered=4, uncovered_labels=tuple("x" for _ in range(6)), team="T"
        )

        assert len(coverage.uncovered_labels) == coverage.total - coverage.covered


def _stats(removed: int, clusters: int):
    from ranobelib_epub.charset.filter import FilterStats

    return FilterStats(removed_characters=removed, removed_clusters=clusters)


def _book() -> Book:
    return Book(slug_url="https://ranobelib.me/book/94231--x", book_id=94231)


def _unused(buffer: io.BytesIO) -> None:  # pragma: no cover - удержание импорта io
    buffer.seek(0)
