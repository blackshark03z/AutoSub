"""Cross-lane E1+E2 integration binding conformance tests.

Part of AutoSub US-first integration acceptance (review/us-e1-e2-crosslane-20261017).
Verifies the seam between the E1 multimarket contracts/cache and the E2 offline
runtime on *real* artifacts when present, plus deterministic contract behavior
otherwise:
  1. One immutable OCR master -> fan-out into multiple locale L3 keys (single pass).
  2. Locale glossary isolation: changing one market's glossary only re-keys that market.
  3. Source-video change invalidates L1 -> L2 -> L3 (no cross-source reuse).
  4. E2 cube bridge: E1 master cue (raw_ocr_text) -> E2 render cue (zh_text) mapping.
  5. Hardened L5 opacity: only finite 1.0 accepted (NaN/inf/sub-1.0 rejected).
  6. Hardened L3 provenance: ambiguous provenance is refused (cannot be tagged live).
  7. LIVE_INFERENCE contract requires an InferenceAuditRecord (anti-forgery).
  8. Real acceptance artifacts (when present): 19/19 cues, offline provenance, live L3.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.hy_mt2_offline_adapter import HyMt2OfflineLocalizer, normalize_cue
from app.services.multimarket_cache import (
    MultiMarketCacheStore,
    compute_l1_key,
    compute_l2_key,
    compute_l3_key,
    compute_l5_key,
)
from app.services.multimarket_contracts import (
    InferenceAuditRecord,
    LocaleTranslationSet,
    LocalizedCue,
    SourceCorrectionReviewLayer,
    SourceZhCue,
    SourceZhMasterCueSet,
    SpatialBoundingBox,
)

REPO = Path(__file__).resolve().parents[1]
ART = REPO / "scratch" / "acceptance_us"


def _box(left: int, top: int, right: int, bottom: int) -> SpatialBoundingBox:
    return SpatialBoundingBox(left_x=left, top_y=top, right_x=right, bottom_y=bottom,
                              width=right - left + 1, height=bottom - top + 1)


def make_master(video_sha: str = "a" * 64) -> SourceZhMasterCueSet:
    return SourceZhMasterCueSet(
        source_video_sha256=video_sha,
        video_width=1280, video_height=720, fps=15.0, total_frames=900, duration_ms=60000,
        ocr_engine_version="RapidOCR_PP-OCRv5_ONNX_v1.4.4",
        sampling_policy_id="sample_fps=2.0_step_ms=500",
        roi_filter_version="roi_dialogue_band_v1.2",
        cues=(
            SourceZhCue(cue_id="cue_001", start_ms=1000, end_ms=2000, raw_ocr_text="徒儿",
                        ocr_confidence=0.91, raw_bbox=_box(587, 596, 691, 652),
                        plate_box=_box(584, 593, 694, 655), frame_count=1,
                        source_frame_provenance="rep_frame_sha:deadbeef"),
        ),
    )


def make_audit() -> InferenceAuditRecord:
    return InferenceAuditRecord(
        invocation_id="inv-1", runtime_backend="tencent_hymt2_q4_llama_cpp",
        model_id="tencent/Hy-MT2-1.8B-Q4_K_M", model_weights_sha256="c" * 64,
        prompt_sha256="d" * 64, input_text_hash="e" * 64, output_text_hash="f" * 64,
        execution_timestamp_iso="2026-10-17T00:00:00+0000", verified_by="e2_hymt2_offline_adapter",
    )


def make_locale_set(master: SourceZhMasterCueSet, layer: SourceCorrectionReviewLayer,
                    locale: str, glossary_hash: str, provenance: str = "LIVE_INFERENCE",
                    audit: InferenceAuditRecord | None = None) -> LocaleTranslationSet:
    cue = LocalizedCue(
        cue_id="cue_001", target_locale=locale, model_target_language="English",
        translated_text="Disciple", effective_source_text="徒儿", status="FIT_OK",
        subtitle_start_ms=1000, subtitle_end_ms=2000, source_mask_start_ms=1000,
        source_mask_end_ms=2000, line_breaks=("Disciple",), font_family="Arial",
        effective_font_size_px=32, rendered_width_px=120, rendered_height_px=36,
        chars_per_second=8.0, words_per_minute=60.0, model_id="tencent/Hy-MT2-1.8B-Q4_K_M",
        prompt_version="hy-mt2-official-default-2026-05-20|contextual-v2",
        glossary_version="us-context-2026-10-17", qa_flags=(), provenance=provenance,
    )
    return LocaleTranslationSet(
        source_video_sha256=master.source_video_sha256, extraction_hash=master.extraction_hash,
        correction_layer_hash=layer.correction_layer_hash, target_locale=locale,
        model_target_language="English", model_id="tencent/Hy-MT2-1.8B-Q4_K_M",
        prompt_glossary_hash=glossary_hash, created_at_iso="2026-10-17T00:00:00+0000",
        cues=(cue,), inference_audit=(audit if provenance == "LIVE_INFERENCE" else None),
    )


def empty_layer(master: SourceZhMasterCueSet) -> SourceCorrectionReviewLayer:
    return SourceCorrectionReviewLayer(extraction_hash=master.extraction_hash,
                                       layer_version="v1", corrections=())


def l1_of(master: SourceZhMasterCueSet) -> str:
    return compute_l1_key(
        source_video_sha256=master.source_video_sha256, video_width=master.video_width,
        video_height=master.video_height, ocr_engine_version=master.ocr_engine_version,
        sampling_policy_id=master.sampling_policy_id, roi_filter_version=master.roi_filter_version)


def l3_of(l2: str, locale: str, glossary_hash: str) -> str:
    return compute_l3_key(
        l2_cache_key=l2, target_locale=locale, model_target_language="English",
        model_id="tencent/Hy-MT2-1.8B-Q4_K_M", model_weights_sha256="c" * 64,
        runtime_backend="tencent_hymt2_q4_llama_cpp", prompt_template_sha256="d" * 64,
        glossary_sha256=glossary_hash, context_window_size=1)


def test_single_master_fans_out_to_distinct_locale_keys():
    master = make_master()
    layer = empty_layer(master)
    l1 = l1_of(master)
    l2 = compute_l2_key(l1_cache_key=l1, correction_layer_hash=layer.correction_layer_hash)
    us = l3_of(l2, "en-US", "us-glossary-sha")
    vi = l3_of(l2, "vi-VN", "vi-glossary-sha")
    assert us != vi
    assert l1_of(master) == l1  # one OCR pass -> stable shared master key


def test_locale_glossary_isolation():
    master = make_master()
    l2 = compute_l2_key(l1_cache_key=l1_of(master),
                        correction_layer_hash=empty_layer(master).correction_layer_hash)
    us_before = l3_of(l2, "en-US", "us-glossary-v1")
    vi_before = l3_of(l2, "vi-VN", "vi-glossary-v1")
    us_after = l3_of(l2, "en-US", "us-glossary-v2")  # only US glossary changed
    assert us_after != us_before
    assert l3_of(l2, "vi-VN", "vi-glossary-v1") == vi_before  # vi untouched


def test_source_change_invalidates_all_tiers():
    a, b = make_master("a" * 64), make_master("b" * 64)
    la = compute_l2_key(l1_cache_key=l1_of(a),
                        correction_layer_hash=empty_layer(a).correction_layer_hash)
    lb = compute_l2_key(l1_cache_key=l1_of(b),
                        correction_layer_hash=empty_layer(b).correction_layer_hash)
    assert l1_of(a) != l1_of(b)
    assert la != lb
    assert l3_of(la, "en-US", "g") != l3_of(lb, "en-US", "g")


def test_e2_cue_bridge_from_e1_master():
    master = make_master()
    cue = master.cues[0]
    assert not hasattr(cue, "zh_text")  # E1 stores raw_ocr_text, not E2 key
    bridged = {"cue_id": cue.cue_id, "start_ms": cue.start_ms, "end_ms": cue.end_ms,
               "zh_text": cue.raw_ocr_text}
    cue_id, start_ms, end_ms, text = normalize_cue(bridged)
    assert (cue_id, start_ms, end_ms, text) == ("cue_001", 1000, 2000, "徒儿")


@pytest.mark.parametrize("opacity", [0.9, 0.0, -1.0, float("nan"), float("inf")])
def test_l5_opacity_hardening_rejects_non_unity(opacity):
    with pytest.raises(ValueError):
        compute_l5_key(l4_cache_key="x", source_video_sha256="a" * 64, audio_stream_index=1,
                       ffmpeg_version="9.0.1", render_preset="p", plate_opacity=opacity)


def test_l5_opacity_accepts_exactly_one():
    key = compute_l5_key(l4_cache_key="x", source_video_sha256="a" * 64, audio_stream_index=1,
                         ffmpeg_version="9.0.1", render_preset="p", plate_opacity=1.0)
    assert isinstance(key, str) and len(key) == 64


def test_live_inference_requires_audit_record():
    master = make_master()
    with pytest.raises(ValueError):
        LocaleTranslationSet(
            source_video_sha256=master.source_video_sha256, extraction_hash=master.extraction_hash,
            correction_layer_hash=empty_layer(master).correction_layer_hash, target_locale="en-US",
            model_target_language="English", model_id="m", prompt_glossary_hash="g",
            created_at_iso="t", cues=(LocalizedCue(
                cue_id="c", target_locale="en-US", model_target_language="English",
                translated_text="x", effective_source_text="y", status="FIT_OK",
                subtitle_start_ms=0, subtitle_end_ms=1, source_mask_start_ms=0, source_mask_end_ms=1,
                line_breaks=("x",), font_family="Arial", effective_font_size_px=32,
                rendered_width_px=1, rendered_height_px=1, chars_per_second=1.0, words_per_minute=1.0,
                model_id="m", prompt_version="p", glossary_version="g", provenance="LIVE_INFERENCE"),),
            inference_audit=None)


def test_ambiguous_provenance_cannot_be_cached_as_live(tmp_path):
    master = make_master()
    layer = empty_layer(master)
    l1 = l1_of(master)
    l2 = compute_l2_key(l1_cache_key=l1, correction_layer_hash=layer.correction_layer_hash)
    store = MultiMarketCacheStore(tmp_path / "c.db")
    store.put_l1_extraction(l1, master)
    store.put_l2_correction(l2, l1, layer)
    l3 = l3_of(l2, "en-US", "g")
    ambiguous = make_locale_set(master, layer, "en-US", "g", provenance="DRAFT_UNKNOWN")
    with pytest.raises(ValueError):
        store.put_l3_translation(l3, l2, ambiguous)


def test_real_acceptance_artifacts_offline_and_live():
    zh = ART / "translations_en-US.json"
    vi = ART / "translations_vi-VN.json"
    if not zh.exists() or not vi.exists():
        pytest.skip("real acceptance artifacts not present")
    for path in (zh, vi):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["cue_count"] == 19
        assert data["translated_count"] == 19
        assert data["runtime_backend"] == "tencent_hymt2_q4_llama_cpp"
        assert data["provenance"].startswith("llama.cpp_hymt2_q4_")
        assert data["l3_live_cache_ok"] is True
        assert data["inference_audit"]["verified_by"] == "e2_hymt2_offline_adapter"
        for r in data["results"]:
            assert r["status"] == "TRANSLATED"
            assert r["translation"].strip()
            assert not any("\u4e00" <= c <= "\u9fff" for c in r["translation"])

    master = ART / "master_cues.json"
    if master.exists():
        loaded = SourceZhMasterCueSet.from_json(master.read_text(encoding="utf-8"))
        assert len(loaded.cues) == 19
        meta = json.loads((ART / "ocr_meta.json").read_text(encoding="utf-8"))
        assert meta["cue_count"] == 19
        assert meta["l1_roundtrip_ok"] is True
        assert loaded.extraction_hash == meta["master_extraction_hash"]
