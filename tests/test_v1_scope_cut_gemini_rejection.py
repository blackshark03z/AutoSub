"""
V1 SCOPE-CUT: Test that Gemini source-caption modes are properly rejected.
"""
import pytest
from pathlib import Path
from app.services.simple_workflow import (
    create_or_reuse_run,
    SOURCE_CAPTION_GEMINI_MODE,
    SOURCE_CAPTION_HUMAN_REVIEW_MODE,
    SOURCE_CAPTION_MODE,
)
from app.core.config import get_settings


HTML_PATH = Path("app/static/simple/index.html")


def test_ui_does_not_expose_gemini_mode():
    """UI should not show Gemini source-caption option to users."""
    html = HTML_PATH.read_text(encoding="utf-8")
    
    # Gemini mode should not be visible
    assert "source_caption_gemini_translation" not in html
    assert "Gemini free tier" not in html.lower()
    assert "Gemini" not in html.split('id="cleanupMode"')[1].split("</select>")[0]
    
    # OCR mode should be the only option
    assert 'value="source_caption_ocr_translation"' in html
    assert html.count('id="cleanupMode"') == 1


def test_ui_stable_default_mode_present():
    """UI should present the stable OCR translation mode."""
    html = HTML_PATH.read_text(encoding="utf-8")
    
    cleanup_section = html.split('id="cleanupMode"')[1].split("</select>")[0]
    assert "source_caption_ocr_translation" in cleanup_section
    assert "OCR" in cleanup_section or "ocr" in cleanup_section.lower()


def test_precreate_guard_logic_present():
    """create_or_reuse_run should have explicit rejection logic before run creation."""
    workflow_file = Path("app/services/simple_workflow.py").read_text(encoding="utf-8")
    
    # Check pre-create guard exists
    create_fn = workflow_file.split("def create_or_reuse_run")[1].split("\ndef ")[0]
    assert "SOURCE_CAPTION_GEMINI_MODE" in create_fn
    assert "SOURCE_CAPTION_HUMAN_REVIEW_MODE" in create_fn
    assert "not available in V1" in create_fn
    # Guard must come before run_id creation
    guard_pos = create_fn.find("SOURCE_CAPTION_GEMINI_MODE")
    run_id_pos = create_fn.find("run_id = _new_run_id")
    assert guard_pos > 0, "Pre-create guard not found"
    assert run_id_pos > 0, "Run ID creation not found"
    assert guard_pos < run_id_pos, "Guard must come before run_id creation"


def test_defensive_guard_retained():
    """accept_processing should retain defensive guard as backup."""
    workflow_file = Path("app/services/simple_workflow.py").read_text(encoding="utf-8")
    
    accept_fn = workflow_file.split("def accept_processing")[1].split("\ndef ")[0]
    # Defensive guard should still exist
    assert "SOURCE_CAPTION_GEMINI_MODE" in accept_fn or "V1 SCOPE-CUT" in accept_fn


def test_default_settings_use_external_local_transcription_mode():
    """DEFAULT_SETTINGS should use the approved external local engine, not OCR or Gemini."""
    workflow_file = Path("app/services/simple_workflow.py").read_text(encoding="utf-8")
    
    default_settings = workflow_file.split("DEFAULT_SETTINGS = {")[1].split("}")[0]
    assert "caption_mode" in default_settings
    assert "EXTERNAL_AUDIO_MODE" in default_settings
    # Should not default to local audio or Gemini
    assert '"caption_mode": LOCAL_AUDIO_MODE' not in default_settings
    assert "SOURCE_CAPTION_GEMINI_MODE" not in default_settings.split("caption_mode")[1].split("\n")[0]


# Behavioral tests that verify actual rejection
def _get_test_video():
    """Get a test video path for behavioral tests."""
    # Use a real file from the evidence directory if available
    evidence_videos = list(Path("evidence").rglob("*.mp4")) if Path("evidence").exists() else []
    if evidence_videos:
        return str(evidence_videos[0])
    # Fall back to a fixture path that might exist
    fixtures = [
        Path("tests/fixtures/tiny.mp4"),
        Path("fixtures/tiny.mp4"),
    ]
    for f in fixtures:
        if f.exists():
            return str(f)
    pytest.skip("No test video available for behavioral test")


def _count_runs():
    """Count existing runs in the database."""
    from app.db.session import session_scope
    from app.domain.models import SimpleWorkflowRun
    with session_scope() as session:
        return session.query(SimpleWorkflowRun).count()


def _count_run_directories():
    """Count run directories on disk."""
    settings = get_settings()
    projects_dir = settings.data_dir / "projects"
    if not projects_dir.exists():
        return 0
    count = 0
    for project_dir in projects_dir.iterdir():
        if project_dir.is_dir():
            runs_dir = project_dir / "runs"
            if runs_dir.exists():
                count += sum(1 for d in runs_dir.iterdir() if d.is_dir())
    return count


def test_gemini_mode_rejected_before_run_creation():
    """Gemini mode request should be rejected before creating run in DB or filesystem."""
    try:
        video_path = _get_test_video()
    except pytest.skip.Exception:
        pytest.skip("No test video available")
    
    baseline_run_count = _count_runs()
    baseline_dir_count = _count_run_directories()
    
    # Attempt to create run with Gemini mode
    with pytest.raises(ValueError, match="Gemini source-caption translation is not available in V1"):
        create_or_reuse_run(video_path, settings={"caption_mode": SOURCE_CAPTION_GEMINI_MODE})
    
    # Verify no run was created
    assert _count_runs() == baseline_run_count, "Run count should not increase"
    assert _count_run_directories() == baseline_dir_count, "No new run directory should be created"


def test_human_review_mode_rejected_before_run_creation():
    """Human review mode request should also be rejected before run creation."""
    try:
        video_path = _get_test_video()
    except pytest.skip.Exception:
        pytest.skip("No test video available")
    
    baseline_run_count = _count_runs()
    baseline_dir_count = _count_run_directories()
    
    with pytest.raises(ValueError, match="Gemini source-caption translation is not available in V1"):
        create_or_reuse_run(video_path, settings={"caption_mode": SOURCE_CAPTION_HUMAN_REVIEW_MODE})
    
    assert _count_runs() == baseline_run_count, "Run count should not increase"
    assert _count_run_directories() == baseline_dir_count, "No new run directory should be created"


def test_ocr_mode_accepted_at_creation():
    """OCR mode should pass pre-create validation."""
    try:
        video_path = _get_test_video()
    except pytest.skip.Exception:
        pytest.skip("No test video available")
    
    baseline_run_count = _count_runs()
    
    # This should succeed
    result = create_or_reuse_run(video_path, settings={"caption_mode": SOURCE_CAPTION_MODE})
    
    assert result["run_id"], "Run should be created"
    # If not reused, run count should increase
    if not result.get("reused"):
        assert _count_runs() > baseline_run_count, "Run count should increase for new run"


