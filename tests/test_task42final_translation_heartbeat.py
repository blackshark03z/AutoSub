from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError

import pytest

from app.services.caption_analysis_runtime import CaptionAnalysisError
from app.services.source_caption_translation import _await_translation_batch_with_heartbeat


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProgress:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update(self, **changes: object) -> None:
        self.updates.append(changes)


class TimedFuture:
    def __init__(self, clock: FakeClock, *, complete_at: float, result: object) -> None:
        self.clock = clock
        self.complete_at = complete_at
        self.value = result
        self.result_calls = 0
        self.cancel_calls = 0

    def result(self, timeout: float) -> object:
        self.result_calls += 1
        self.clock.advance(timeout)
        if self.clock.now >= self.complete_at:
            return self.value
        raise FutureTimeoutError()

    def cancel(self) -> bool:
        self.cancel_calls += 1
        return True


def _await(future: object, progress: FakeProgress, clock: FakeClock, *, timeout_seconds: float = 180.0) -> object:
    return _await_translation_batch_with_heartbeat(
        future,
        progress=progress,
        current_item_id="translation_batch_01",
        timeout_seconds=timeout_seconds,
        poll_seconds=5.0,
        monotonic=clock.monotonic,
    )


def test_long_translation_future_heartbeats_and_returns_one_result() -> None:
    clock = FakeClock()
    progress = FakeProgress()
    result = ({"OCR_0001": {"translated_text": "Hello"}}, {"request_count": 1}, [])
    future = TimedFuture(clock, complete_at=130.0, result=result)

    assert _await(future, progress, clock) is result
    assert future.result_calls == 26
    assert future.cancel_calls == 0
    assert len(progress.updates) == 25
    assert all(update == {
        "force": True,
        "analysis_stage": "translation_resolution",
        "current_item_id": "translation_batch_01",
    } for update in progress.updates)


def test_fast_translation_future_returns_without_heartbeat_or_duplicate_wait() -> None:
    clock = FakeClock()
    progress = FakeProgress()
    result = ({"OCR_0001": {"translated_text": "Hello"}}, {"request_count": 1}, [])
    future = TimedFuture(clock, complete_at=0.0, result=result)

    assert _await(future, progress, clock) is result
    assert future.result_calls == 1
    assert future.cancel_calls == 0
    assert progress.updates == []


def test_translation_future_timeout_cancels_only_the_timed_out_future() -> None:
    clock = FakeClock()
    progress = FakeProgress()
    future = TimedFuture(clock, complete_at=181.0, result=object())

    with pytest.raises(CaptionAnalysisError) as caught:
        _await(future, progress, clock)

    assert caught.value.code == "CAPTION_TRANSLATION_BATCH_TIMEOUT"
    assert str(caught.value) == "Caption translation batch timed out"
    assert future.cancel_calls == 1
    assert future.result_calls == 36
    assert len(progress.updates) == 36


def test_translation_future_provider_exception_propagates_unchanged() -> None:
    class ProviderFailure(RuntimeError):
        pass

    class FailingFuture:
        def __init__(self, error: Exception) -> None:
            self.error = error
            self.cancel_calls = 0

        def result(self, timeout: float) -> object:
            raise self.error

        def cancel(self) -> bool:
            self.cancel_calls += 1
            return True

    clock = FakeClock()
    progress = FakeProgress()
    error = ProviderFailure("provider failed")
    future = FailingFuture(error)

    with pytest.raises(ProviderFailure) as caught:
        _await(future, progress, clock)

    assert caught.value is error
    assert future.cancel_calls == 0
    assert progress.updates == []
