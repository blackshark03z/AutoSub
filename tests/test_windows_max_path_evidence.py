import json
import os
import sys
from pathlib import Path
import pytest
from app.core.paths import windows_safe_path, safe_write_text, safe_read_text


def test_windows_max_path_evidence_handling(tmp_path: Path):
    """Regression test: simulate directory layout exceeding 260 chars on Windows.
    Verifies that safe_write_text and safe_read_text correctly write and read
    evidence without triggering Win32 MAX_PATH (260 char) errors.
    """
    # Build a deeply nested path to exceed 260 chars
    # e.g., tmp_path / nested_dir_1 / nested_dir_2 / ...
    deep_segments = ["very_long_project_evidence_directory_level_" + str(i) for i in range(1, 5)]
    nested_dir = tmp_path
    for seg in deep_segments:
        nested_dir = nested_dir / seg
    
    filename = "post_narration_projection_evidence.json"
    target_file = nested_dir / filename
    
    # Calculate length
    total_len = len(str(target_file.resolve()))
    # Ensure it's deep enough (at least 240+ chars, ideally 260+)
    if total_len < 265:
        padding = "a" * (270 - total_len)
        target_file = nested_dir / padding / filename

    resolved_len = len(str(target_file.resolve()))
    assert resolved_len >= 240, f"Path length {resolved_len} should test extended path behavior"

    test_data = {
        "status": "PASS",
        "phase": "post_narration_alignment",
        "cues_evaluated": 15,
        "evidence_id": "EV-MAXPATH-REGRESSION-001",
        "message": "Evidence written safely under long path condition",
    }
    content = json.dumps(test_data, indent=2, ensure_ascii=False)
    
    # Write using safe_write_text
    safe_write_text(target_file, content)
    
    # Check that file exists (via windows_safe_path)
    safe_p = windows_safe_path(target_file)
    assert safe_p.exists(), f"Target file should exist: {safe_p}"
    
    # Read using safe_read_text
    read_back = safe_read_text(target_file)
    parsed = json.loads(read_back)
    assert parsed["status"] == "PASS"
    assert parsed["evidence_id"] == "EV-MAXPATH-REGRESSION-001"


def test_clean_subtitle_render_ass_writing_on_max_path_directory(tmp_path: Path):
    """P1 Regression test: prove real module write/read path of clean_subtitle_render
    (write_clean_subtitles_ass and safe_write_text for manifests) works on a directory
    exceeding 260 chars on Windows without FileNotFoundError.
    """
    from app.services.clean_subtitle_render import write_clean_subtitles_ass

    # Build directory exceeding 260 chars
    deep_path = tmp_path
    for segment in ["evidence_storage_container_tier_alpha", "work_package_pilot_execution_artifacts", "deeply_nested_regression_verification_logs"]:
        deep_path = deep_path / segment
    
    total_len = len(str(deep_path.resolve()))
    if total_len < 250:
        deep_path = deep_path / ("x" * (265 - total_len))

    subtitles_dir = deep_path / "subtitles"
    ass_path = subtitles_dir / "dialogue_subtitles_vi.ass"
    
    resolved_path_str = str(ass_path.resolve())
    assert len(resolved_path_str) >= 250, f"Target path {len(resolved_path_str)} must test long path"

    cues = [
        {"cue_id": "CUE_0001", "start_ms": 1000, "end_ms": 3000, "text": "陈长安", "resolved_text": "Trần Trường An"}
    ]
    layouts = [
        {
            "segment_id": "CUE_0001",
            "overlay_mode": "LOCAL_SUB_MASK_OVERLAY",
            "render_text": "Trần Trường An",
            "font_size": 32,
            "center_x": 640,
            "anchor_y": 640,
            "status": "PASS",
        }
    ]

    # Write ASS via real module function
    written_ass = write_clean_subtitles_ass(
        cues,
        layouts,
        ass_path,
        font_size=32,
        output_width=1280,
        output_height=720,
    )

    # Read back and verify
    assert windows_safe_path(written_ass).exists()
    ass_content = safe_read_text(written_ass)
    assert "Trần Trường An" in ass_content

    # Write manifest in same deep path via safe_write_text
    manifest_path = subtitles_dir / "render_manifest.json"
    manifest_data = {"status": "PASS", "render_mode": "LOCAL_SUB_MASK_OVERLAY", "cues": 1}
    safe_write_text(manifest_path, json.dumps(manifest_data, indent=2), encoding="utf-8")
    
    assert windows_safe_path(manifest_path).exists()
    read_manifest = json.loads(safe_read_text(manifest_path))
    assert read_manifest["render_mode"] == "LOCAL_SUB_MASK_OVERLAY"

