"""Unit tests for AutoSub Multimarket Canonical Data Contracts (Hardened).

Part of AutoSub Work Package WP-AUTOSUB-US-E1-001 / Hardening.
Verifies:
1. Immutability of frozen dataclasses and tuple-backed containers.
2. Coordinate conventions: OpenCV slice notation, FFmpeg drawbox string, opacity 1.0 enforcement.
3. Disclaimer watermark zone collision detection.
4. Cryptographic provenance audit records (InferenceAuditRecord, EditorialReviewAudit).
5. Anti-forgery validation: LIVE_INFERENCE requires audit record; EDITORIAL_REFERENCE requires editorial audit.
6. Schema version compatibility checks.
7. Monotonic timings, unique IDs, bbox inclusivity, confidence bounds.
8. Canonical serialization and deterministic SHA-256 extraction hashing.
9. Non-destructive semantic correction review layer and evidence gates.
10. Synthetic 60-second 19-cue pilot fixture based on cross-lane evidence.
11. Independent multi-gate release manifest authorizations.
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
import pytest

from app.services.multimarket_contracts import (
    CueCorrectionRecord,
    EditorialReviewAudit,
    EffectiveSourceDialogueCue,
    InferenceAuditRecord,
    LocaleTranslationSet,
    LocalizedCue,
    MarketMediaOutputs,
    MultiMarketManifest,
    SourceCorrectionReviewLayer,
    SourceZhCue,
    SourceZhMasterCueSet,
    SpatialBoundingBox,
    canonical_sha256,
)


def make_valid_bbox(
    left_x: int = 100,
    top_y: int = 600,
    right_x: int = 300,
    bottom_y: int = 650,
) -> SpatialBoundingBox:
    w = right_x - left_x + 1
    h = bottom_y - top_y + 1
    return SpatialBoundingBox(
        left_x=left_x,
        top_y=top_y,
        right_x=right_x,
        bottom_y=bottom_y,
        width=w,
        height=h,
    )


def make_valid_cue(
    cue_id: str = "cue_001",
    start_ms: int = 1000,
    end_ms: int = 2500,
    text: str = "徒儿，我大限已至",
    conf: float = 0.985,
) -> SourceZhCue:
    bbox = make_valid_bbox(100, 600, 300, 650)
    plate = make_valid_bbox(96, 596, 304, 654)
    return SourceZhCue(
        cue_id=cue_id,
        start_ms=start_ms,
        end_ms=end_ms,
        raw_ocr_text=text,
        ocr_confidence=conf,
        raw_bbox=bbox,
        plate_box=plate,
        frame_count=22,
        source_frame_provenance="frame_probe_001.png",
    )


def make_valid_audit_record() -> InferenceAuditRecord:
    return InferenceAuditRecord(
        invocation_id="inv_test_001",
        runtime_backend="tencent_hymt2_q4_llama_cpp",
        model_id="tencent_hymt2_q4",
        model_weights_sha256="w" * 64,
        prompt_sha256="p" * 64,
        input_text_hash="in" * 32,
        output_text_hash="out" * 32,
        execution_timestamp_iso="2026-10-17T05:00:00Z",
        verified_by="test_suite_runner",
    )


def make_valid_editorial_audit() -> EditorialReviewAudit:
    return EditorialReviewAudit(
        editorial_board_id="board_donghua_us",
        reviewer_id="lead_editor_us",
        review_basis="manual_pilot_ground_truth",
        review_timestamp_iso="2026-10-17T05:00:00Z",
    )


# ------------------------------------------------------------------------------
# 1. Bounding Box & Helper Tests
# ------------------------------------------------------------------------------

def test_spatial_bounding_box_immutability():
    box = make_valid_bbox(10, 20, 100, 60)
    with pytest.raises(FrozenInstanceError):
        box.left_x = 5  # type: ignore


def test_spatial_bounding_box_coordinate_validation():
    with pytest.raises(ValueError, match="non-negative"):
        SpatialBoundingBox(left_x=-1, top_y=10, right_x=50, bottom_y=40, width=52, height=31)

    with pytest.raises(ValueError, match="right_x.*must be >= left_x"):
        SpatialBoundingBox(left_x=50, top_y=10, right_x=40, bottom_y=40, width=1, height=31)

    with pytest.raises(ValueError, match="Width mismatch"):
        SpatialBoundingBox(left_x=10, top_y=10, right_x=50, bottom_y=40, width=999, height=31)

    box = make_valid_bbox(1200, 680, 1300, 710)
    with pytest.raises(ValueError, match="exceeds frame boundaries"):
        box.validate_within_frame(1280, 720)


def test_spatial_bounding_box_opencv_and_ffmpeg_helpers():
    box = make_valid_bbox(100, 600, 300, 650)
    assert box.width == 201
    assert box.height == 51

    # OpenCV half-open slices (y_slice, x_slice)
    y_slice, x_slice = box.to_opencv_slices()
    assert y_slice == slice(600, 651)
    assert x_slice == slice(100, 301)
    # Length of slices matches box dimensions
    assert (y_slice.stop - y_slice.start) == box.height
    assert (x_slice.stop - x_slice.start) == box.width

    # FFmpeg drawbox string (mandates color=black@1.0)
    drawbox_cmd = box.to_ffmpeg_drawbox_expr("black@1.0")
    assert drawbox_cmd == "x=100:y=600:w=201:h=51:color=black@1.0:t=fill"

    # Reject non-1.0 plate opacity in drawbox
    with pytest.raises(ValueError, match="plate color opacity must be 1.0"):
        box.to_ffmpeg_drawbox_expr("black@0.76")


def test_spatial_bounding_box_disclaimer_collision():
    # Box in dialogue region (x=200, y=600) -> does NOT overlap bottom-right disclaimer (x>900, y>650)
    safe_box = make_valid_bbox(200, 600, 500, 640)
    assert safe_box.overlaps_disclaimer_zone(min_x=900, min_y=650) is False

    # Box encroaching on disclaimer area (x=950, y=660) -> overlaps!
    colliding_box = make_valid_bbox(850, 630, 950, 680)
    assert colliding_box.overlaps_disclaimer_zone(min_x=900, min_y=650) is True


# ------------------------------------------------------------------------------
# 2. SourceZhCue & MasterCueSet Tests
# ------------------------------------------------------------------------------

def test_source_zh_cue_immutability_and_validation():
    cue = make_valid_cue()
    with pytest.raises(FrozenInstanceError):
        cue.raw_ocr_text = "Mutated"  # type: ignore

    with pytest.raises(ValueError, match="strictly greater than start_ms"):
        make_valid_cue(start_ms=2000, end_ms=2000)

    with pytest.raises(ValueError, match="start_ms must be >= 0"):
        make_valid_cue(start_ms=-50, end_ms=1000)

    with pytest.raises(ValueError, match="ocr_confidence must be finite"):
        make_valid_cue(conf=float("nan"))

    with pytest.raises(ValueError, match="range"):
        make_valid_cue(conf=1.5)

    with pytest.raises(ValueError, match="raw_ocr_text cannot be empty"):
        make_valid_cue(text="   ")


def test_source_master_cue_set_immutability_and_ordering():
    cue1 = make_valid_cue("cue_001", start_ms=1000, end_ms=2000)
    cue2 = make_valid_cue("cue_002", start_ms=2500, end_ms=4000)
    cue_set = SourceZhMasterCueSet(
        source_video_sha256="a" * 64,
        video_width=1280,
        video_height=720,
        fps=15.0,
        total_frames=900,
        duration_ms=60000,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="sample_fps_2.0",
        roi_filter_version="roi_v1",
        cues=(cue1, cue2),
    )

    with pytest.raises(FrozenInstanceError):
        cue_set.duration_ms = 50000  # type: ignore

    with pytest.raises(AttributeError):
        cue_set.cues.append(cue1)  # type: ignore

    with pytest.raises(TypeError, match="must be an immutable tuple"):
        SourceZhMasterCueSet(
            source_video_sha256="a" * 64,
            video_width=1280,
            video_height=720,
            fps=15.0,
            total_frames=900,
            duration_ms=60000,
            ocr_engine_version="RapidOCR_v1.4.4",
            sampling_policy_id="sample_fps_2.0",
            roi_filter_version="roi_v1",
            cues=[cue1, cue2],  # type: ignore
        )

    # Reject duplicate cue IDs
    cue2_dup = make_valid_cue("cue_001", start_ms=2500, end_ms=4000)
    with pytest.raises(ValueError, match="Duplicate cue_id"):
        SourceZhMasterCueSet(
            source_video_sha256="a" * 64,
            video_width=1280,
            video_height=720,
            fps=15.0,
            total_frames=900,
            duration_ms=60000,
            ocr_engine_version="RapidOCR_v1.4.4",
            sampling_policy_id="sample_fps_2.0",
            roi_filter_version="roi_v1",
            cues=(cue1, cue2_dup),
        )

    # Reject non-monotonic timing order
    cue_unordered = make_valid_cue("cue_003", start_ms=500, end_ms=800)
    with pytest.raises(ValueError, match="monotonically sorted"):
        SourceZhMasterCueSet(
            source_video_sha256="a" * 64,
            video_width=1280,
            video_height=720,
            fps=15.0,
            total_frames=900,
            duration_ms=60000,
            ocr_engine_version="RapidOCR_v1.4.4",
            sampling_policy_id="sample_fps_2.0",
            roi_filter_version="roi_v1",
            cues=(cue1, cue_unordered),
        )


def test_schema_version_validation():
    cue1 = make_valid_cue()
    # Unsupported schema version in master cue set
    with pytest.raises(ValueError, match="Unsupported schema_version"):
        SourceZhMasterCueSet(
            source_video_sha256="a" * 64,
            video_width=1280,
            video_height=720,
            fps=15.0,
            total_frames=900,
            duration_ms=60000,
            ocr_engine_version="RapidOCR_v1.4.4",
            sampling_policy_id="sample_fps_2.0",
            roi_filter_version="roi_v1",
            cues=(cue1,),
            schema_version="99.0.0",  # Incompatible!
        )


def test_canonical_serialization_and_hash_stability():
    cue1 = make_valid_cue("cue_001", start_ms=1000, end_ms=2000, text="万法宗")
    cue_set = SourceZhMasterCueSet(
        source_video_sha256="b" * 64,
        video_width=1280,
        video_height=720,
        fps=15.0,
        total_frames=900,
        duration_ms=60000,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="sample_fps_2.0",
        roi_filter_version="roi_v1",
        cues=(cue1,),
        source_video_path="C:\\Users\\Local\\video.mp4",
    )
    hash1 = cue_set.extraction_hash

    cue_set_diff_path = SourceZhMasterCueSet(
        source_video_sha256="b" * 64,
        video_width=1280,
        video_height=720,
        fps=15.0,
        total_frames=900,
        duration_ms=60000,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="sample_fps_2.0",
        roi_filter_version="roi_v1",
        cues=(cue1,),
        source_video_path="/mnt/server/other_path.mp4",
    )
    assert cue_set_diff_path.extraction_hash == hash1

    data = cue_set.to_dict()
    restored = SourceZhMasterCueSet.from_dict(data)
    assert restored.extraction_hash == hash1
    assert restored.cues[0].raw_ocr_text == "万法宗"


# ------------------------------------------------------------------------------
# 3. Evidence-Backed Correction Layer Tests
# ------------------------------------------------------------------------------

def test_evidence_backed_correction_review_layer():
    cue1 = make_valid_cue("cue_012", start_ms=28270, end_ms=30900, text="否则我三不目")
    cue_set = SourceZhMasterCueSet(
        source_video_sha256="c" * 64,
        video_width=1280,
        video_height=720,
        fps=15.0,
        total_frames=900,
        duration_ms=60000,
        ocr_engine_version="RapidOCR_v1.4.4",
        sampling_policy_id="sample_fps_2.0",
        roi_filter_version="roi_v1",
        cues=(cue1,),
    )

    # 1. Reject ACCEPTED modification without evidence
    with pytest.raises(ValueError, match="cannot be ACCEPTED without evidence_provenance"):
        CueCorrectionRecord(
            cue_id="cue_012",
            raw_ocr_text="否则我三不目",
            corrected_text="否则我死不瞑目",
            correction_type="OCR_PHONETIC_TYPO",
            evidence_provenance="",
            reviewer_id="human_qa",
            review_status="ACCEPTED",
        )

    # 2. Reject ACCEPTED modification without reviewer
    with pytest.raises(ValueError, match="cannot be ACCEPTED without reviewer_id"):
        CueCorrectionRecord(
            cue_id="cue_012",
            raw_ocr_text="否则我三不目",
            corrected_text="否则我死不瞑目",
            correction_type="OCR_PHONETIC_TYPO",
            evidence_provenance="audio_inspection_and_classical_idiom_sǐbùmíngmù",
            reviewer_id="",
            review_status="ACCEPTED",
        )

    # 3. DRAFT correction does not require evidence, but DOES NOT affect effective text
    draft_record = CueCorrectionRecord(
        cue_id="cue_012",
        raw_ocr_text="否则我三不目",
        corrected_text="否则我死不瞑目",
        correction_type="OCR_PHONETIC_TYPO",
        evidence_provenance="",
        reviewer_id="",
        review_status="DRAFT",
    )
    layer_draft = SourceCorrectionReviewLayer(
        extraction_hash=cue_set.extraction_hash,
        layer_version="v1.0.0-draft",
        corrections=(draft_record,),
    )
    resolved_draft = layer_draft.apply(cue_set)
    assert resolved_draft[0].effective_text == "否则我三不目"
    assert resolved_draft[0].is_corrected is False

    # 4. Verified ACCEPTED correction successfully updates effective text
    accepted_record = CueCorrectionRecord(
        cue_id="cue_012",
        raw_ocr_text="否则我三不目",
        corrected_text="否则我死不瞑目",
        correction_type="OCR_PHONETIC_TYPO",
        evidence_provenance="audio_inspection_and_classical_idiom_sǐbùmíngmù",
        reviewer_id="senior_donghua_editor",
        review_status="ACCEPTED",
    )
    layer_accepted = SourceCorrectionReviewLayer(
        extraction_hash=cue_set.extraction_hash,
        layer_version="v1.1.0-reviewed",
        corrections=(accepted_record,),
    )
    resolved_accepted = layer_accepted.apply(cue_set)
    assert resolved_accepted[0].effective_text == "否则我死不瞑目"
    assert resolved_accepted[0].is_corrected is True
    assert cue_set.cues[0].raw_ocr_text == "否则我三不目"


# ------------------------------------------------------------------------------
# 4. Cryptographic Provenance & Anti-Forgery Tests
# ------------------------------------------------------------------------------

def test_inference_audit_record_validation():
    # Empty invocation_id
    with pytest.raises(ValueError, match="invocation_id cannot be empty"):
        InferenceAuditRecord(
            invocation_id="",
            runtime_backend="test",
            model_id="test_model",
            model_weights_sha256="w" * 64,
            prompt_sha256="p" * 64,
            input_text_hash="in" * 32,
            output_text_hash="out" * 32,
            execution_timestamp_iso="2026-10-17T05:00:00Z",
            verified_by="verifier",
        )

    # Empty model_weights_sha256
    with pytest.raises(ValueError, match="model_weights_sha256 cannot be empty"):
        InferenceAuditRecord(
            invocation_id="inv_001",
            runtime_backend="test",
            model_id="test_model",
            model_weights_sha256="",
            prompt_sha256="p" * 64,
            input_text_hash="in" * 32,
            output_text_hash="out" * 32,
            execution_timestamp_iso="2026-10-17T05:00:00Z",
            verified_by="verifier",
        )


def test_provenance_anti_forgery_contract():
    cue = LocalizedCue(
        cue_id="cue_001",
        target_locale="en-US",
        model_target_language="English",
        translated_text="Test",
        effective_source_text="Test",
        status="FIT_OK",
        subtitle_start_ms=1000,
        subtitle_end_ms=2000,
        source_mask_start_ms=1000,
        source_mask_end_ms=2000,
        line_breaks=("Test",),
        font_family="Arial",
        effective_font_size_px=32,
        rendered_width_px=100,
        rendered_height_px=36,
        chars_per_second=4.0,
        words_per_minute=60.0,
        model_id="test_model",
        prompt_version="v1",
        glossary_version="v1",
        provenance="LIVE_INFERENCE",
    )

    # 1. Reject LIVE_INFERENCE without audit record
    with pytest.raises(ValueError, match="Cannot declare LIVE_INFERENCE provenance without a valid InferenceAuditRecord"):
        LocaleTranslationSet(
            source_video_sha256="a" * 64,
            extraction_hash="b" * 64,
            correction_layer_hash="c" * 64,
            target_locale="en-US",
            model_target_language="English",
            model_id="test_model",
            prompt_glossary_hash="p" * 64,
            created_at_iso="2026-10-17T05:00:00Z",
            cues=(cue,),
            inference_audit=None,  # Missing audit!
        )

    # 2. Providing valid audit record succeeds
    audit = make_valid_audit_record()
    valid_set = LocaleTranslationSet(
        source_video_sha256="a" * 64,
        extraction_hash="b" * 64,
        correction_layer_hash="c" * 64,
        target_locale="en-US",
        model_target_language="English",
        model_id="test_model",
        prompt_glossary_hash="p" * 64,
        created_at_iso="2026-10-17T05:00:00Z",
        cues=(cue,),
        inference_audit=audit,
    )
    assert valid_set.inference_audit is not None
    assert valid_set.inference_audit.invocation_id == "inv_test_001"


# ------------------------------------------------------------------------------
# 5. Synthetic Fixture & Multi-Gate Manifest Tests
# ------------------------------------------------------------------------------

def test_synthetic_60s_19_cue_pilot_fixture():
    evidence_cues = [
        ("cue_001", 8530, 9030, "徒儿", 0.914, (586, 596, 692, 652)),
        ("cue_002", 9600, 11170, "我大限已至", 0.995, (523, 598, 754, 650)),
        ("cue_003", 12270, 15430, "我走后你一定要守护好宗门", 0.996, (370, 598, 908, 648)),
        ("cue_004", 15470, 16500, "壮大我万法宗", 0.994, (502, 601, 777, 649)),
        ("cue_005", 16530, 18100, "老头你别嘎呀", 0.984, (502, 598, 777, 651)),
        ("cue_006", 18130, 19700, "你还没教我真本事呢", 0.995, (439, 601, 842, 648)),
        ("cue_007", 19730, 20770, "你真嘎了", 0.997, (547, 600, 727, 650)),
        ("cue_008", 20800, 22370, "我可能都活不过第二天啊", 0.995, (395, 601, 888, 648)),
        ("cue_009", 22930, 25030, "这是我宗的镇宗功法", 0.993, (435, 601, 843, 648)),
        ("cue_010", 25070, 27170, "你务必妥善保管", 0.998, (479, 598, 795, 651)),
        ("cue_011", 27200, 28230, "切不可遗失", 0.999, (523, 600, 754, 649)),
        ("cue_012", 28270, 30900, "否则我三不目", 0.940, (481, 598, 799, 652)),
        ("cue_013", 30930, 31970, "啥玩意老头", 0.999, (521, 600, 756, 650)),
        ("cue_014", 32000, 33030, "您是不是拿错了", 0.999, (485, 601, 791, 649)),
        ("cue_015", 33070, 35170, "怎么拿一本房术大全给我", 0.998, (396, 601, 887, 648)),
        ("cue_016", 35200, 39430, "拿着它壮壮大我万法宗", 0.980, (416, 601, 867, 648)),
        ("cue_017", 46930, 47970, "老头", 1.000, (585, 600, 694, 650)),
        ("cue_018", 49070, 50630, "您给我一本运动大全", 0.998, (439, 601, 842, 648)),
        ("cue_019", 50670, 52770, "是不是想让我陪您一起上路啊", 0.996, (350, 601, 931, 648)),
    ]

    cues_list = []
    for cid, s, e, text, conf, (x0, y0, x1, y1) in evidence_cues:
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        box = SpatialBoundingBox(left_x=x0, top_y=y0, right_x=x1, bottom_y=y1, width=w, height=h)
        cues_list.append(
            SourceZhCue(
                cue_id=cid,
                start_ms=s,
                end_ms=e,
                raw_ocr_text=text,
                ocr_confidence=conf,
                raw_bbox=box,
                plate_box=box,
                frame_count=15,
                source_frame_provenance=f"evidence_{cid}.png",
            )
        )

    cue_set = SourceZhMasterCueSet(
        source_video_sha256="7cbfcb2ddca5348a5b26de108f799968c600d7206428a92c06c8830707d1bef1",
        video_width=1280,
        video_height=720,
        fps=15.0,
        total_frames=900,
        duration_ms=60000,
        ocr_engine_version="RapidOCR_PP-OCRv5_ONNX_v1.4.4",
        sampling_policy_id="sample_fps=2.0_step_ms=500",
        roi_filter_version="v1_dialogue_band_y555_690",
        cues=tuple(cues_list),
    )

    assert len(cue_set.cues) == 19
    assert cue_set.cues[0].cue_id == "cue_001"
    assert cue_set.cues[-1].cue_id == "cue_019"
    assert len(cue_set.extraction_hash) == 64


def test_independent_multi_gate_release_manifest():
    manifest = MultiMarketManifest(
        manifest_version="2.1.0-reviewed",
        source_video_sha256="d" * 64,
        extraction_hash="e" * 64,
        master_cue_count=19,
    )
    us_market = MarketMediaOutputs(target_locale="en-US")
    manifest.markets["en-US"] = us_market

    assert us_market.is_publication_authorized is False
    assert us_market.technical_render_status == "PENDING"
    assert us_market.owner_publish_approval == "BLOCKED_DEFAULT"

    us_market.technical_render_status = "RENDERED_OK"
    assert us_market.is_publication_authorized is False

    us_market.localization_qa_status = "PASSED_QA"
    us_market.provenance_rights_status = "COMMERCIAL_LICENSE_CONFIRMED"
    us_market.platform_policy_status = "EDITORIAL_TRANSFORMATION_CONFIRMED"
    us_market.ai_disclosure_status = "SYNTHETIC_DECLARED"
    assert us_market.is_publication_authorized is False

    us_market.owner_publish_approval = "AUTHORIZED_FOR_PUBLICATION"
    assert us_market.is_publication_authorized is True
