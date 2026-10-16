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
