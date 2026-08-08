import pytest

from app.services.source_caption_inventory import (
    PIXEL_REVIEW_PROVENANCE,
    TECH_LEAD_AUTHORITY,
    MANUAL_REVIEW_PROVENANCE,
    apply_reviewed_window_decisions,
    freeze_caption_inventory,
    operator_review_record_hash,
    reconcile_checkpoint_results,
    validate_operator_review_record,
)


SOURCE_SHA = "67e710166d98c732d6ecadb66c71fa86e376b5971d1d96d713788723094a97da"


def _window(window_id: str, start: float, end: float, *, left: int = 620) -> dict:
    return {
        "window_id": window_id,
        "start_time": start,
        "end_time": end,
        "source_bbox": {"left_x": left, "top_y": 916, "right_x": 1180, "bottom_y": 1034},
        "schema_version": "source_caption_pixel_coverage_v1",
    }


def test_candidate_id_is_deterministic_and_order_independent():
    windows = [_window("caption_active_0002", 10.0, 11.0), _window("caption_active_0001", 1.0, 2.0)]

    first = freeze_caption_inventory(windows, source_video_sha256=SOURCE_SHA)
    second = freeze_caption_inventory(reversed(windows), source_video_sha256=SOURCE_SHA)

    assert [item.window_id for item in first] == ["caption_active_0001", "caption_active_0002"]
    assert [item.candidate_id for item in first] == [item.candidate_id for item in second]


def test_resume_reconciliation_does_not_expand_candidate_universe():
    candidates = freeze_caption_inventory(
        [_window("caption_active_0001", 10.0, 20.0), _window("caption_active_0002", 30.0, 40.0)],
        source_video_sha256=SOURCE_SHA,
    )
    results = {
        "VIS_0001": {"group": {"id": "VIS_0001", "start_time": 11.0, "end_time": 12.0, "bbox": candidates[0].source_bbox}, "english": "One"},
        "VIS_0002": {"group": {"id": "VIS_0002", "start_time": 12.5, "end_time": 13.0, "bbox": candidates[0].source_bbox}, "english": "One again"},
        "VIS_9999": {"group": {"id": "VIS_9999", "start_time": 90.0, "end_time": 91.0, "bbox": candidates[0].source_bbox}, "english": "Orphan"},
    }

    report = reconcile_checkpoint_results(candidates, results, visual_groups=[])

    assert set(report["retained_by_window"]) == {"caption_active_0001", "caption_active_0002"}
    assert len(report["retained_by_window"]["caption_active_0001"]) == 2
    assert report["duplicates"] == [{"result_id": "VIS_0002", "window_id": "caption_active_0001", "reason": "extra_fragment_same_window"}]
    assert report["orphans"] == [{"result_id": "VIS_9999", "reason": "orphan_no_authoritative_window"}]


def test_orphan_result_without_group_is_rejected():
    candidates = freeze_caption_inventory([_window("caption_active_0001", 1.0, 2.0)], source_video_sha256=SOURCE_SHA)

    report = reconcile_checkpoint_results(candidates, {"VIS_missing": {"english": "No group"}}, visual_groups=[])

    assert report["retained_by_window"]["caption_active_0001"] == []
    assert report["orphans"] == [{"result_id": "VIS_missing", "reason": "missing_visual_group"}]


def test_different_windows_with_same_geometry_do_not_merge():
    candidates = freeze_caption_inventory(
        [_window("caption_active_0001", 1.0, 2.0), _window("caption_active_0002", 3.0, 4.0)],
        source_video_sha256=SOURCE_SHA,
    )

    assert len({item.candidate_id for item in candidates}) == 2


def test_operator_review_record_validation_requires_provenance_and_hash():
    record = {
        "video_sha256": SOURCE_SHA,
        "window_ids": ["caption_active_0001"],
        "start_time": 1.0,
        "end_time": 2.0,
        "caption_fingerprint": "abc123",
        "chinese": "我来了",
        "english": "I'm here.",
        "provenance": MANUAL_REVIEW_PROVENANCE,
    }
    record["record_hash"] = operator_review_record_hash(record)

    validate_operator_review_record(record)

    bad = dict(record)
    bad["provenance"] = "gemini_generated"
    with pytest.raises(ValueError, match="provenance"):
        validate_operator_review_record(bad)


def test_operator_review_record_rejects_hash_mismatch():
    record = {
        "video_sha256": SOURCE_SHA,
        "window_ids": ["caption_active_0001"],
        "start_time": 1.0,
        "end_time": 2.0,
        "caption_fingerprint": "abc123",
        "chinese": "我来了",
        "english": "I'm here.",
        "provenance": MANUAL_REVIEW_PROVENANCE,
        "record_hash": "not-the-hash",
    }

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_operator_review_record(record)


def test_apply_reviewed_window_decisions_excludes_hud_only_windows():
    candidates = freeze_caption_inventory([_window("caption_active_0002", 9.742, 10.525)], source_video_sha256=SOURCE_SHA)
    result = apply_reviewed_window_decisions(
        candidates,
        [
            {
                "window_id": "caption_active_0002",
                "action": "HUD_NON_CAPTION",
                "provenance": PIXEL_REVIEW_PROVENANCE,
                "authority": TECH_LEAD_AUTHORITY,
                "reason": "visible ofte only",
            }
        ],
        source_video_sha256=SOURCE_SHA,
    )

    assert result["instances"] == []
    assert result["exclusions"] == [
        {
            "window_id": "caption_active_0002",
            "candidate_id": candidates[0].candidate_id,
            "status": "HUD_NON_CAPTION",
            "provenance": PIXEL_REVIEW_PROVENANCE,
            "authority": TECH_LEAD_AUTHORITY,
            "reason": "visible ofte only",
        }
    ]
    assert result["unreviewed_window_ids"] == []


def test_apply_reviewed_window_decisions_splits_mixed_window_into_reviewed_instances():
    candidates = freeze_caption_inventory([_window("caption_active_0016", 149.208, 154.792)], source_video_sha256=SOURCE_SHA)
    result = apply_reviewed_window_decisions(
        candidates,
        [
            {
                "window_id": "caption_active_0016",
                "action": "SPLIT_CAPTION_WINDOW",
                "provenance": PIXEL_REVIEW_PROVENANCE,
                "authority": TECH_LEAD_AUTHORITY,
                "segments": [
                    {"start_time": 154.0, "end_time": 154.792, "chinese": "我早就会了", "english": "I already knew how."}
                ],
            }
        ],
        source_video_sha256=SOURCE_SHA,
    )

    assert len(result["instances"]) == 1
    instance = result["instances"][0]
    assert instance.window_id == "caption_active_0016"
    assert instance.chinese == "我早就会了"
    assert instance.english == "I already knew how."
    assert instance.provenance == PIXEL_REVIEW_PROVENANCE
    assert instance.authority == TECH_LEAD_AUTHORITY
    assert instance.record_hash
    assert result["unreviewed_window_ids"] == []


def test_apply_reviewed_window_decisions_rejects_wrong_authority():
    candidates = freeze_caption_inventory([_window("caption_active_0022", 196.675, 204.925)], source_video_sha256=SOURCE_SHA)
    with pytest.raises(ValueError, match="authority"):
        apply_reviewed_window_decisions(
            candidates,
            [
                {
                    "window_id": "caption_active_0022",
                    "action": "SPLIT_CAPTION_WINDOW",
                    "provenance": PIXEL_REVIEW_PROVENANCE,
                    "authority": "WRONG",
                    "segments": [{"start_time": 196.675, "end_time": 198.0, "chinese": "哦雷霆天要去", "english": "Oh, Lei Tingtian is leaving."}],
                }
            ],
            source_video_sha256=SOURCE_SHA,
        )
