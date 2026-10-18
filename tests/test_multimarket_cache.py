"""Unit tests for AutoSub Multimarket 5-Tier SQLite Cache (Hardened).

Part of AutoSub Work Package WP-AUTOSUB-US-E1-001 / Hardening.
Verifies:
1. Deterministic SHA-256 keys for all 5 tiers (L1 through L5) with weights and shaping engine.
2. Mandatory opaque plate opacity 1.0 enforcement (rejection of sub-1.0 values).
3. SQLite CRUD round-trip fidelity with SHA-256 payload checksums.
4. Corruption fail-closed detection: tampered payloads are deleted and return cache miss.
5. Transaction rollback atomicity on error: no partial dirty rows.
6. Concurrent multi-thread reader/writer safety in WAL mode.
7. Anti-cache poisoning: audit record required for LIVE_INFERENCE; editorial drafts isolated.
8. Fine-grained invalidation matrix across tiers, locales, and fonts.
"""

from __future__ import annotations

import concurrent.futures
import sqlite3
import pytest

from app.services.multimarket_cache import (
    MultiMarketCacheStore,
    compute_l1_key,
    compute_l2_key,
    compute_l3_key,
    compute_l4_key,
    compute_l5_key,
)
from app.services.multimarket_contracts import (
    CueCorrectionRecord,
    EditorialReviewAudit,
    InferenceAuditRecord,
    LocaleTranslationSet,
    LocalizedCue,
    SourceCorrectionReviewLayer,
    SourceZhCue,
    SourceZhMasterCueSet,
    SpatialBoundingBox,
)


def make_sample_master_cues(video_sha: str = "1" * 64) -> SourceZhMasterCueSet:
    box = SpatialBoundingBox(left_x=100, top_y=600, right_x=300, bottom_y=650, width=201, height=51)
    cue = SourceZhCue(
        cue_id="cue_001",
        start_ms=1000,
        end_ms=2500,
        raw_ocr_text="徒儿，我大限已至",
        ocr_confidence=0.99,
        raw_bbox=box,
        plate_box=box,
        frame_count=20,
        source_frame_provenance="probe_001.png",
    )
    return SourceZhMasterCueSet(
        source_video_sha256=video_sha,
        video_width=1280,
        video_height=720,
        fps=15.0,
        total_frames=900,
        duration_ms=60000,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="sample_fps_2.0",
        roi_filter_version="roi_v1",
        cues=(cue,),
    )


def make_sample_audit() -> InferenceAuditRecord:
    return InferenceAuditRecord(
        invocation_id="inv_cache_test_001",
        runtime_backend="tencent_hymt2_q4_llama_cpp",
        model_id="tencent_hymt2_q4",
        model_weights_sha256="w" * 64,
        prompt_sha256="p" * 64,
        input_text_hash="in" * 32,
        output_text_hash="out" * 32,
        execution_timestamp_iso="2026-10-17T05:00:00Z",
        verified_by="model_runner_guard",
    )


def make_sample_editorial_audit() -> EditorialReviewAudit:
    return EditorialReviewAudit(
        editorial_board_id="board_donghua_us",
        reviewer_id="lead_editor_us",
        review_basis="manual_pilot_ground_truth",
        review_timestamp_iso="2026-10-17T05:00:00Z",
    )


# ------------------------------------------------------------------------------
# 1. Key Formulas & Opacity Invariant Tests
# ------------------------------------------------------------------------------

def test_deterministic_5_tier_key_formulas():
    # L1 key with weights hash
    l1_key1 = compute_l1_key(
        source_video_sha256="a" * 64,
        video_width=1280,
        video_height=720,
        ocr_engine_version="RapidOCR_v1.4.4",
        ocr_weights_sha256="weights_v5_001",
        sampling_policy_id="fps_2.0",
        roi_filter_version="roi_v1",
    )
    l1_key2 = compute_l1_key(
        source_video_sha256="a" * 64,
        video_width=1280,
        video_height=720,
        ocr_engine_version="RapidOCR_v1.4.4",
        ocr_weights_sha256="weights_v5_001",
        sampling_policy_id="fps_2.0",
        roi_filter_version="roi_v1",
    )
    assert l1_key1 == l1_key2
    assert len(l1_key1) == 64

    # Changing OCR weights changes L1 key
    l1_diff_weights = compute_l1_key(
        source_video_sha256="a" * 64,
        video_width=1280,
        video_height=720,
        ocr_engine_version="RapidOCR_v1.4.4",
        ocr_weights_sha256="weights_v5_002_retrained",  # Changed!
        sampling_policy_id="fps_2.0",
        roi_filter_version="roi_v1",
    )
    assert l1_diff_weights != l1_key1

    # L2 key
    l2_key = compute_l2_key(l1_cache_key=l1_key1, correction_layer_hash="corr_hash_001")
    assert len(l2_key) == 64

    # L3 key with model weights
    l3_key_us = compute_l3_key(
        l2_cache_key=l2_key,
        target_locale="en-US",
        model_target_language="English",
        model_id="tencent_hymt2_q4",
        model_weights_sha256="gguf_q4_hash_001",
        runtime_backend="cpu_llama_cpp",
        prompt_template_sha256="p" * 64,
        glossary_sha256="g_us" * 16,
    )
    assert len(l3_key_us) == 64

    # L4 key with shaping engine
    l4_key = compute_l4_key(
        l3_cache_key=l3_key_us,
        plate_geometry_hash="geom_001",
        font_family="Arial",
        font_file_sha256="f" * 64,
        wrap_algo_version="v2_syntactic",
        video_width=1280,
        video_height=720,
        shaping_engine="libass_v1",
    )
    assert len(l4_key) == 64

    # L5 key with mandatory plate_opacity=1.0
    l5_key = compute_l5_key(
        l4_cache_key=l4_key,
        source_video_sha256="a" * 64,
        audio_stream_index=0,
        audio_stream_sha256="audio_001",
        ffmpeg_version="ffmpeg_6.1",
        render_preset="libx264_crf18",
        plate_opacity=1.0,
    )
    assert len(l5_key) == 64

    # REJECTION of sub-1.0 plate opacity in L5 key computation
    with pytest.raises(ValueError, match="plate_opacity must be 1.0"):
        compute_l5_key(
            l4_cache_key=l4_key,
            source_video_sha256="a" * 64,
            audio_stream_index=0,
            ffmpeg_version="ffmpeg_6.1",
            render_preset="libx264_crf18",
            plate_opacity=0.76,  # Forbidden!
        )


# ------------------------------------------------------------------------------
# 2. SQLite CRUD, Checksums & Corruption Fail-Closed
# ------------------------------------------------------------------------------

def test_sqlite_cache_crud_round_trip(tmp_path):
    db_file = tmp_path / "test_cache.db"
    store = MultiMarketCacheStore(db_file)

    cue_set = make_sample_master_cues("a" * 64)
    l1_key = compute_l1_key(
        source_video_sha256="a" * 64,
        video_width=1280,
        video_height=720,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="fps_2.0",
        roi_filter_version="roi_v1",
    )

    # 1. Put and get L1
    store.put_l1_extraction(l1_key, cue_set)
    recovered_l1 = store.get_l1_extraction(l1_key)
    assert recovered_l1 is not None
    assert recovered_l1.extraction_hash == cue_set.extraction_hash
    assert recovered_l1.cues[0].raw_ocr_text == "徒儿，我大限已至"

    # 2. Put and get L2
    rec = CueCorrectionRecord(
        cue_id="cue_001",
        raw_ocr_text="徒儿，我大限已至",
        corrected_text="徒儿，我大限已至",
        correction_type="NONE",
        evidence_provenance="audio_matched",
        reviewer_id="editor_1",
        review_status="ACCEPTED",
    )
    layer = SourceCorrectionReviewLayer(
        extraction_hash=cue_set.extraction_hash,
        layer_version="v1.0.0",
        corrections=(rec,),
    )
    l2_key = compute_l2_key(l1_cache_key=l1_key, correction_layer_hash=layer.correction_layer_hash)
    store.put_l2_correction(l2_key, l1_key, layer)
    recovered_l2 = store.get_l2_correction(l2_key)
    assert recovered_l2 is not None
    assert recovered_l2.correction_layer_hash == layer.correction_layer_hash

    # 3. Put and get L3 (with valid audit record)
    audit = make_sample_audit()
    loc_cue = LocalizedCue(
        cue_id="cue_001",
        target_locale="en-US",
        model_target_language="English",
        translated_text="My time has come.",
        effective_source_text="徒儿，我大限已至",
        status="FIT_OK",
        subtitle_start_ms=1000,
        subtitle_end_ms=2500,
        source_mask_start_ms=1000,
        source_mask_end_ms=2500,
        line_breaks=("My time has come.",),
        font_family="Arial",
        effective_font_size_px=32,
        rendered_width_px=271,
        rendered_height_px=36,
        chars_per_second=11.3,
        words_per_minute=160.0,
        model_id="tencent_hymt2_q4",
        prompt_version="v2.1",
        glossary_version="v1.0",
        provenance="LIVE_INFERENCE",
    )
    trans_set = LocaleTranslationSet(
        source_video_sha256="a" * 64,
        extraction_hash=cue_set.extraction_hash,
        correction_layer_hash=layer.correction_layer_hash,
        target_locale="en-US",
        model_target_language="English",
        model_id="tencent_hymt2_q4",
        prompt_glossary_hash="pghash_001",
        created_at_iso="2026-10-17T05:00:00Z",
        cues=(loc_cue,),
        inference_audit=audit,
    )
    l3_key = compute_l3_key(
        l2_cache_key=l2_key,
        target_locale="en-US",
        model_target_language="English",
        model_id="tencent_hymt2_q4",
        runtime_backend="cpu_llama_cpp",
        prompt_template_sha256="p" * 64,
        glossary_sha256="g" * 64,
    )
    store.put_l3_translation(l3_key, l2_key, trans_set)
    recovered_l3 = store.get_l3_translation(l3_key)
    assert recovered_l3 is not None
    assert recovered_l3["target_locale"] == "en-US"
    assert recovered_l3["cues"][0]["translated_text"] == "My time has come."
    assert recovered_l3["inference_audit"]["invocation_id"] == "inv_cache_test_001"


def test_sqlite_cache_checksum_corruption_fail_closed(tmp_path):
    """If payload_json is tampered with on disk, checksum mismatch triggers fail-closed cleanup."""
    db_file = tmp_path / "corrupt_test.db"
    store = MultiMarketCacheStore(db_file)

    cue_set = make_sample_master_cues("a" * 64)
    l1_key = compute_l1_key(
        source_video_sha256="a" * 64,
        video_width=1280,
        video_height=720,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="fps_2.0",
        roi_filter_version="roi_v1",
    )
    store.put_l1_extraction(l1_key, cue_set)
    assert store.get_l1_extraction(l1_key) is not None

    # Manually tamper with raw payload in SQLite table without updating checksum
    with store._get_connection() as conn:
        conn.execute(
            "UPDATE cache_l1_extractions SET payload_json = '{\"tampered\": true}' WHERE cache_key = ?",
            (l1_key,),
        )

    # Retrieval must detect checksum discrepancy, purge entry, and return None
    assert store.get_l1_extraction(l1_key) is None

    # Verify corrupt row was deleted
    with store._get_connection() as conn:
        row = conn.execute("SELECT * FROM cache_l1_extractions WHERE cache_key = ?", (l1_key,)).fetchone()
        assert row is None


def test_sqlite_transaction_rollback_on_error(tmp_path):
    """Simulate mid-transaction failure and ensure no partial state is committed."""
    db_file = tmp_path / "rollback_test.db"
    store = MultiMarketCacheStore(db_file)

    with pytest.raises(RuntimeError, match="Simulated crash"):
        with store._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO cache_l1_extractions (
                    cache_key, source_video_sha256, extraction_hash, payload_json, payload_checksum
                )
                VALUES ('test_key', 'test_video', 'test_hash', '{}', 'test_chk')
                """
            )
            # Simulated crash before commit
            raise RuntimeError("Simulated crash")

    # Assert row was rolled back completely
    with store._get_connection() as conn:
        row = conn.execute("SELECT * FROM cache_l1_extractions WHERE cache_key = 'test_key'").fetchone()
        assert row is None


def test_sqlite_concurrent_readers_and_writers(tmp_path):
    """Verify multi-threaded concurrent writes and reads succeed cleanly in WAL mode."""
    db_file = tmp_path / "concurrency_test.db"
    store = MultiMarketCacheStore(db_file)

    def worker_write(index: int) -> str:
        key = f"key_{index:04d}"
        cue_set = make_sample_master_cues(f"{index:04d}" * 16)
        store.put_l1_extraction(key, cue_set)
        retrieved = store.get_l1_extraction(key)
        assert retrieved is not None
        return key

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(worker_write, i) for i in range(24)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 24
    with store._get_connection() as conn:
        count = conn.execute("SELECT count(*) as cnt FROM cache_l1_extractions").fetchone()["cnt"]
        assert count == 24


# ------------------------------------------------------------------------------
# 3. Anti-Cache Poisoning & Invalidation Matrix
# ------------------------------------------------------------------------------

def test_anti_cache_poisoning_audit_verification(tmp_path):
    db_file = tmp_path / "poison_test.db"
    store = MultiMarketCacheStore(db_file)

    cue_set = make_sample_master_cues("b" * 64)
    l1_key = compute_l1_key(
        source_video_sha256="b" * 64,
        video_width=1280,
        video_height=720,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="fps_2.0",
        roi_filter_version="roi_v1",
    )
    store.put_l1_extraction(l1_key, cue_set)

    layer = SourceCorrectionReviewLayer(
        extraction_hash=cue_set.extraction_hash,
        layer_version="v1.0",
        corrections=(),
    )
    l2_key = compute_l2_key(l1_cache_key=l1_key, correction_layer_hash=layer.correction_layer_hash)
    store.put_l2_correction(l2_key, l1_key, layer)

    # Cue marked as EDITORIAL_REFERENCE
    editorial_audit = make_sample_editorial_audit()
    editorial_cue = LocalizedCue(
        cue_id="cue_001",
        target_locale="en-US",
        model_target_language="English",
        translated_text="Editorial sample text",
        effective_source_text="徒儿",
        status="FIT_OK",
        subtitle_start_ms=1000,
        subtitle_end_ms=2000,
        source_mask_start_ms=1000,
        source_mask_end_ms=2000,
        line_breaks=("Editorial sample text",),
        font_family="Arial",
        effective_font_size_px=32,
        rendered_width_px=300,
        rendered_height_px=36,
        chars_per_second=21.0,
        words_per_minute=126.0,
        model_id="manual_editor",
        prompt_version="none",
        glossary_version="none",
        provenance="EDITORIAL_REFERENCE",
    )
    editorial_set = LocaleTranslationSet(
        source_video_sha256="b" * 64,
        extraction_hash=cue_set.extraction_hash,
        correction_layer_hash=layer.correction_layer_hash,
        target_locale="en-US",
        model_target_language="English",
        model_id="manual_editor",
        prompt_glossary_hash="none",
        created_at_iso="2026-10-17T05:00:00Z",
        cues=(editorial_cue,),
        editorial_audit=editorial_audit,
    )
    l3_key = compute_l3_key(
        l2_cache_key=l2_key,
        target_locale="en-US",
        model_target_language="English",
        model_id="manual_editor",
        runtime_backend="manual",
        prompt_template_sha256="0" * 64,
        glossary_sha256="0" * 64,
    )

    # 1. By default, putting editorial translation without allow_editorial=True raises ValueError
    with pytest.raises(ValueError, match="Refusing to cache editorial reference"):
        store.put_l3_translation(l3_key, l2_key, editorial_set, allow_editorial=False)

    # 2. Putting with allow_editorial=True succeeds
    store.put_l3_translation(l3_key, l2_key, editorial_set, allow_editorial=True)

    # 3. Reading with require_live_inference=True (default) returns None (cache miss)
    assert store.get_l3_translation(l3_key, require_live_inference=True) is None

    # 4. Reading with require_live_inference=False returns the record
    assert store.get_l3_translation(l3_key, require_live_inference=False) is not None


def test_invalidation_matrix(tmp_path):
    """Test precise multi-tier invalidation rules."""
    db_file = tmp_path / "invalidation_test.db"
    store = MultiMarketCacheStore(db_file)

    cue_set = make_sample_master_cues("c" * 64)
    l1_key = compute_l1_key(
        source_video_sha256="c" * 64,
        video_width=1280,
        video_height=720,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="fps_2.0",
        roi_filter_version="roi_v1",
    )
    store.put_l1_extraction(l1_key, cue_set)

    layer = SourceCorrectionReviewLayer(
        extraction_hash=cue_set.extraction_hash,
        layer_version="v1.0",
        corrections=(),
    )
    l2_key = compute_l2_key(l1_cache_key=l1_key, correction_layer_hash=layer.correction_layer_hash)
    store.put_l2_correction(l2_key, l1_key, layer)

    # Seed en-US translation (L3) with audit
    audit = make_sample_audit()
    cue_us = LocalizedCue(
        cue_id="cue_001",
        target_locale="en-US",
        model_target_language="English",
        translated_text="English",
        effective_source_text="徒儿",
        status="FIT_OK",
        subtitle_start_ms=1000,
        subtitle_end_ms=2000,
        source_mask_start_ms=1000,
        source_mask_end_ms=2000,
        line_breaks=("English",),
        font_family="Arial",
        effective_font_size_px=32,
        rendered_width_px=100,
        rendered_height_px=36,
        chars_per_second=7.0,
        words_per_minute=60.0,
        model_id="hymt2",
        prompt_version="v1",
        glossary_version="v1",
        provenance="LIVE_INFERENCE",
    )
    trans_us = LocaleTranslationSet(
        source_video_sha256="c" * 64,
        extraction_hash=cue_set.extraction_hash,
        correction_layer_hash=layer.correction_layer_hash,
        target_locale="en-US",
        model_target_language="English",
        model_id="hymt2",
        prompt_glossary_hash="pg_us",
        created_at_iso="2026-10-17T05:00:00Z",
        cues=(cue_us,),
        inference_audit=audit,
    )
    l3_key_us = compute_l3_key(
        l2_cache_key=l2_key,
        target_locale="en-US",
        model_target_language="English",
        model_id="hymt2",
        runtime_backend="cpu",
        prompt_template_sha256="1" * 64,
        glossary_sha256="1" * 64,
    )
    store.put_l3_translation(l3_key_us, l2_key, trans_us)

    # Seed vi-VN translation (L3) with audit
    cue_vi = LocalizedCue(
        cue_id="cue_001",
        target_locale="vi-VN",
        model_target_language="Vietnamese",
        translated_text="Tieng Viet",
        effective_source_text="徒儿",
        status="FIT_OK",
        subtitle_start_ms=1000,
        subtitle_end_ms=2000,
        source_mask_start_ms=1000,
        source_mask_end_ms=2000,
        line_breaks=("Tieng Viet",),
        font_family="Arial",
        effective_font_size_px=32,
        rendered_width_px=120,
        rendered_height_px=36,
        chars_per_second=10.0,
        words_per_minute=120.0,
        model_id="hymt2",
        prompt_version="v1",
        glossary_version="v1",
        provenance="LIVE_INFERENCE",
    )
    trans_vi = LocaleTranslationSet(
        source_video_sha256="c" * 64,
        extraction_hash=cue_set.extraction_hash,
        correction_layer_hash=layer.correction_layer_hash,
        target_locale="vi-VN",
        model_target_language="Vietnamese",
        model_id="hymt2",
        prompt_glossary_hash="pg_vi",
        created_at_iso="2026-10-17T05:00:00Z",
        cues=(cue_vi,),
        inference_audit=audit,
    )
    l3_key_vi = compute_l3_key(
        l2_cache_key=l2_key,
        target_locale="vi-VN",
        model_target_language="Vietnamese",
        model_id="hymt2",
        runtime_backend="cpu",
        prompt_template_sha256="2" * 64,
        glossary_sha256="2" * 64,
    )
    store.put_l3_translation(l3_key_vi, l2_key, trans_vi)

    # Seed L4 Layout for en-US
    l4_key_us = compute_l4_key(
        l3_cache_key=l3_key_us,
        plate_geometry_hash="geom",
        font_family="Trebuchet MS",
        font_file_sha256="f" * 64,
        wrap_algo_version="v1",
        video_width=1280,
        video_height=720,
    )
    store.put_l4_layout(l4_key_us, l3_key_us, "en-US", "Trebuchet MS", {"lines": ["English"]})

    # Assert all seeded entries exist
    assert store.get_l3_translation(l3_key_us) is not None
    assert store.get_l3_translation(l3_key_vi) is not None
    assert store.get_l4_layout(l4_key_us) is not None

    # INVARIATION TEST 1: Font Invalidation
    # Invalidate 'Trebuchet MS' font -> L4 cleared, L3 preserved!
    store.invalidate_font("Trebuchet MS")
    assert store.get_l4_layout(l4_key_us) is None
    assert store.get_l3_translation(l3_key_us) is not None  # PRESERVED
    assert store.get_l3_translation(l3_key_vi) is not None  # PRESERVED
    assert store.get_l1_extraction(l1_key) is not None      # PRESERVED

    # Re-seed L4
    store.put_l4_layout(l4_key_us, l3_key_us, "en-US", "Trebuchet MS", {"lines": ["English"]})

    # INVARIATION TEST 2: Locale Invalidation
    # Invalidate 'en-US' locale -> en-US L3/L4 deleted, vi-VN L3 and L1/L2 PRESERVED!
    store.invalidate_locale("en-US")
    assert store.get_l3_translation(l3_key_us) is None
    assert store.get_l4_layout(l4_key_us) is None
    assert store.get_l3_translation(l3_key_vi) is not None  # vi-VN 100% PRESERVED
    assert store.get_l2_correction(l2_key) is not None      # L2 PRESERVED
    assert store.get_l1_extraction(l1_key) is not None      # L1 PRESERVED

    # INVARIATION TEST 3: Correction Invalidation
    # Invalidate L2 correction -> L2, L3 deleted, L1 PRESERVED!
    store.invalidate_correction(l1_key)
    assert store.get_l2_correction(l2_key) is None
    assert store.get_l3_translation(l3_key_vi) is None      # Cascaded deletion
    assert store.get_l1_extraction(l1_key) is not None      # L1 100% PRESERVED
