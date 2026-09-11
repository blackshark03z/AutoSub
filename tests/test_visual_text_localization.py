from dataclasses import dataclass

from app.services.visual_text_localization import localize_visual_text_tracks


@dataclass
class FakeResult:
    translations: list[dict[str, str]]
    model: str = "fake-model"
    request_count: int = 1
    retry_count: int = 0
    cache_hits: int = 0
    cache_misses: int = 1
    input_tokens: int = 10
    output_tokens: int = 5
    request_ids: list[str] | None = None

    def __post_init__(self):
        if self.request_ids is None:
            self.request_ids = ["req-1"]


class FakeTranslator:
    provider_name = "fake_gemini"
    model = "fake-model"

    def __init__(self):
        self.requests = []

    def translate(self, captions, **kwargs):
        self.requests = list(captions)
        return FakeResult([
            {"id": item["id"], "english": f"EN:{item['source_text']}"}
            for item in captions
        ])


def _track(track_id: str, text: str, action: str) -> dict:
    return {
        "track_id": track_id,
        "canonical_text": text,
        "classification": {"action": action, "reason": "fixture"},
        "start_ms": 0,
        "end_ms": 1000,
        "observations": [],
    }


def test_only_localizable_tracks_are_translated_once() -> None:
    translator = FakeTranslator()
    result = localize_visual_text_tracks(
        {"tracks": [
            _track("t1", "管理员", "localize"),
            _track("t2", "2500", "preserve"),
            _track("t3", "所有东西", "localize"),
        ]},
        translator=translator,
    )
    assert [item["id"] for item in translator.requests] == ["t1", "t3"]
    assert result["translated_track_count"] == 2
    by_id = {track["track_id"]: track for track in result["tracks"]}
    assert by_id["t1"]["display_text"] == "EN:管理员"
    assert by_id["t2"]["display_text"] == "2500"
    assert by_id["t2"]["localization_state"] == "preserved"
    assert result["provider_usage"]["request_count"] == 1


def test_duplicate_visual_text_reuses_one_translation_without_merging_tracks() -> None:
    translator = FakeTranslator()
    result = localize_visual_text_tracks(
        {"tracks": [
            _track("a", "管理员", "localize"),
            _track("b", "管理员", "localize"),
            _track("c", "所有东西", "localize"),
        ]},
        translator=translator,
    )
    assert [item["id"] for item in translator.requests] == ["a", "c"]
    assert result["translated_track_count"] == 3
    assert result["translation_request_count"] == 2
    assert result["translation_reuse_count"] == 1
    by_id = {track["track_id"]: track for track in result["tracks"]}
    assert by_id["a"]["display_text"] == by_id["b"]["display_text"] == "EN:管理员"
    assert by_id["a"]["track_id"] != by_id["b"]["track_id"]


def test_track_context_uses_neighboring_localizable_semantics_only() -> None:
    translator = FakeTranslator()
    localize_visual_text_tracks(
        {"tracks": [
            _track("a", "第一", "localize"),
            _track("n", "123", "preserve"),
            _track("b", "第二", "localize"),
        ]},
        translator=translator,
    )
    assert translator.requests[0]["next_source_text"] == "第二"
    assert translator.requests[1]["previous_source_text"] == "第一"


def test_translation_identity_mismatch_fails_closed() -> None:
    class BadTranslator(FakeTranslator):
        def translate(self, captions, **kwargs):
            return FakeResult([{"id": "wrong", "english": "Wrong"}])

    try:
        localize_visual_text_tracks(
            {"tracks": [_track("t1", "管理员", "localize")]},
            translator=BadTranslator(),
        )
    except ValueError as exc:
        assert "identity mismatch" in str(exc)
    else:
        raise AssertionError("mismatched track translation must fail closed")
