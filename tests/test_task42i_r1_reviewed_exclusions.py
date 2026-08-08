import pytest
import json
import hashlib
from app.services.source_caption_translation import _source_caption_coverage_adjustments

def _make_exclusion(window_id, start, end, classification="HUD_NON_CAPTION", video_sha="fake_sha", valid=True, override_hash=None):
    exc = {
        "source_video_sha256": video_sha,
        "window_id": window_id,
        "start_time": start,
        "end_time": end,
        "classification": classification,
        "provenance": "human_reviewed_from_pixels",
        "authority": "TECH_LEAD_APPROVED"
    }
    encoded = json.dumps(exc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    h = override_hash if override_hash else hashlib.sha256(encoded).hexdigest()
    if not valid and not override_hash:
        h = "tampered_" + h
    exc["record_hash"] = h
    return exc

def test_1_valid_hud_exclusion_passes_no_cues():
    windows = [{
        "window_id": "caption_active_0001",
        "start_time": 10.0,
        "end_time": 15.0,
        "representative_times": [10.0, 12.5, 15.0],
        "source_bbox": {"top_y": 900, "bottom_y": 1000},
        "pixel_confidence": 1.0,
        "schema_version": "v1"
    }]
    cues = []
    exclusions = [_make_exclusion("caption_active_0001", 10.0, 15.0, classification="HUD_NON_CAPTION")]
    
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=exclusions)
    
    assert len(unc) == 0
    assert rec[0]["coverage_verdict"] == "PASS_REVIEWED_EXCLUSION"
    assert rec[0]["mask_interval"] is None
    assert rec[0]["english_interval"] is None

def test_2_valid_reviewed_transition_gap():
    windows = [{
        "window_id": "caption_active_0022",
        "start_time": 100.0,
        "end_time": 110.0,
        "representative_times": [100.0, 105.0, 110.0],
        "source_bbox": {"top_y": 900, "bottom_y": 1000},
        "pixel_confidence": 1.0,
        "schema_version": "v1"
    }]
    cues = [
        {"cue_id": "CUE1", "start_ms": 100000, "end_ms": 104000},
        {"cue_id": "CUE2", "start_ms": 106000, "end_ms": 110000},
    ]
    exclusions = [_make_exclusion("caption_active_0022", 104.0, 106.0, classification="REVIEWED_TRANSITION_GAP")]
    
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=exclusions)
    assert len(unc) == 0
    assert rec[0]["coverage_verdict"] == "PASS_MATCHED_SOURCE_CUE"

def test_3_gap_no_exclusion_fails():
    windows = [{
        "window_id": "caption_active_0022",
        "start_time": 100.0,
        "end_time": 110.0,
        "representative_times": [100.0, 105.0, 110.0],
        "source_bbox": {"top_y": 900, "bottom_y": 1000},
        "pixel_confidence": 1.0,
        "schema_version": "v1"
    }]
    cues = [
        {"cue_id": "CUE1", "start_ms": 100000, "end_ms": 104000},
        {"cue_id": "CUE2", "start_ms": 106000, "end_ms": 110000},
    ]
    # Gap is 104-106 but no exclusion
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=[])
    assert len(unc) == 1
    assert unc[0]["coverage_verdict"] == "FAIL_UNCOVERED_SOURCE_CAPTION_GAP"

def test_4_invalid_video_hash_fails():
    windows = [{"window_id": "w1", "start_time": 10.0, "end_time": 15.0, "representative_times": [10.0, 12.5, 15.0], "source_bbox": {}, "pixel_confidence": 1.0, "schema_version": "v1"}]
    cues = []
    # Video sha doesn't match
    exclusions = [_make_exclusion("w1", 10.0, 15.0, video_sha="wrong_sha")]
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=exclusions)
    assert len(unc) == 1

def test_5_out_of_bounds_exclusion_fails():
    windows = [{"window_id": "w1", "start_time": 10.0, "end_time": 15.0, "representative_times": [10.0, 12.5, 15.0], "source_bbox": {}, "pixel_confidence": 1.0, "schema_version": "v1"}]
    cues = []
    # Exclusion goes out of parent bounds
    exclusions = [_make_exclusion("w1", 10.0, 16.0)]
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=exclusions)
    assert len(unc) == 1

def test_6_unknown_classification_fails():
    windows = [{"window_id": "w1", "start_time": 10.0, "end_time": 15.0, "representative_times": [10.0, 12.5, 15.0], "source_bbox": {}, "pixel_confidence": 1.0, "schema_version": "v1"}]
    cues = []
    exclusions = [_make_exclusion("w1", 10.0, 15.0, classification="UNKNOWN_THING")]
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=exclusions)
    assert len(unc) == 1

def test_7_tampered_hash_fails():
    windows = [{"window_id": "w1", "start_time": 10.0, "end_time": 15.0, "representative_times": [10.0, 12.5, 15.0], "source_bbox": {}, "pixel_confidence": 1.0, "schema_version": "v1"}]
    cues = []
    exclusions = [_make_exclusion("w1", 10.0, 15.0, valid=False)]
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=exclusions)
    assert len(unc) == 1

def test_8_partial_exclusion_still_fails():
    windows = [{"window_id": "w1", "start_time": 10.0, "end_time": 15.0, "representative_times": [10.0, 12.5, 15.0], "source_bbox": {}, "pixel_confidence": 1.0, "schema_version": "v1"}]
    cues = []
    # Exclusion only covers 10-14, leaves 14-15 uncovered
    exclusions = [_make_exclusion("w1", 10.0, 14.0)]
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=exclusions)
    assert len(unc) == 1
    assert unc[0]["uncovered_gaps"][0]["start_time"] == 14.0

def test_9_fully_covered_cue_behavior_does_not_regress():
    windows = [{"window_id": "w1", "start_time": 10.0, "end_time": 15.0, "representative_times": [10.0, 12.5, 15.0], "source_bbox": {}, "pixel_confidence": 1.0, "schema_version": "v1"}]
    cues = [{"cue_id": "CUE1", "start_ms": 10000, "end_ms": 15000}]
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=[])
    assert len(unc) == 0

def test_10_eof_coverage_invariant():
    windows = [{"window_id": "w1", "start_time": 10.0, "end_time": 15.0, "representative_times": [10.0, 12.5, 15.0], "source_bbox": {}, "pixel_confidence": 1.0, "schema_version": "v1"}]
    cues = [{"cue_id": "CUE1", "start_ms": 10000, "end_ms": 14000}] # Missing EOF portion
    adj, rec, unc = _source_caption_coverage_adjustments(cues, windows, source_video_sha256="fake_sha", reviewed_coverage_exclusions=[])
    assert len(unc) == 0
