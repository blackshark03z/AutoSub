"""Test V1 fix: prevent reuse across incompatible workflow settings.

This test verifies that create_or_reuse_run() does NOT reuse existing runs
when requested settings differ in output-affecting ways like caption_mode.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.simple_workflow import (
    _settings_compatible_for_reuse,
    SOURCE_CAPTION_MODE,
    LOCAL_AUDIO_MODE,
)
from app.domain.models import SimpleWorkflowRun


def test_settings_compatible_for_reuse_caption_mode_mismatch():
    """Test A: ASR -> OCR different caption mode should NOT be compatible."""
    # Mock existing ASR run
    existing_run = MagicMock(spec=SimpleWorkflowRun)
    existing_run.requested_settings_json = json.dumps({
        "caption_mode": LOCAL_AUDIO_MODE,
        "target_language": "English",
    })
    
    # Requested OCR settings
    requested = {
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
    }
    
    # Should NOT be compatible
    assert _settings_compatible_for_reuse(existing_run, requested) is False


def test_settings_compatible_for_reuse_same_settings():
    """Test B: Same caption mode should be compatible."""
    existing_run = MagicMock(spec=SimpleWorkflowRun)
    existing_run.requested_settings_json = json.dumps({
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
        "subtitle_mode": "burned_into_video",
    })
    
    requested = {
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
        "subtitle_mode": "burned_into_video",
    }
    
    # Should be compatible
    assert _settings_compatible_for_reuse(existing_run, requested) is True


def test_settings_compatible_for_reuse_ocr_to_asr_mismatch():
    """Test C: OCR -> ASR reverse mismatch should NOT be compatible."""
    existing_run = MagicMock(spec=SimpleWorkflowRun)
    existing_run.requested_settings_json = json.dumps({
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
    })
    
    requested = {
        "caption_mode": LOCAL_AUDIO_MODE,
        "target_language": "English",
    }
    
    assert _settings_compatible_for_reuse(existing_run, requested) is False


def test_settings_compatible_for_reuse_target_language_mismatch():
    """Test D: Different target language should NOT be compatible."""
    existing_run = MagicMock(spec=SimpleWorkflowRun)
    existing_run.requested_settings_json = json.dumps({
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
    })
    
    requested = {
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "Spanish",
    }
    
    assert _settings_compatible_for_reuse(existing_run, requested) is False


def test_settings_compatible_for_reuse_subtitle_mode_mismatch():
    """Test: Different subtitle_mode should NOT be compatible."""
    existing_run = MagicMock(spec=SimpleWorkflowRun)
    existing_run.requested_settings_json = json.dumps({
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
        "subtitle_mode": "burned_into_video",
    })
    
    requested = {
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
        "subtitle_mode": "sidecar_only",
    }
    
    assert _settings_compatible_for_reuse(existing_run, requested) is False


def test_settings_compatible_for_reuse_invalid_json():
    """Test: Invalid JSON in existing settings should return False."""
    existing_run = MagicMock(spec=SimpleWorkflowRun)
    existing_run.requested_settings_json = "invalid json {"
    
    requested = {
        "caption_mode": SOURCE_CAPTION_MODE,
    }
    
    # Should not be compatible (can't parse existing settings)
    assert _settings_compatible_for_reuse(existing_run, requested) is False


def test_settings_compatible_extra_keys_ignored():
    """Test: Extra keys in requested settings should not affect compatibility."""
    existing_run = MagicMock(spec=SimpleWorkflowRun)
    existing_run.requested_settings_json = json.dumps({
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
    })
    
    requested = {
        "caption_mode": SOURCE_CAPTION_MODE,
        "target_language": "English",
        "extra_key": "some_value",  # Extra key not in output_affecting_keys
    }
    
    # Should still be compatible (extra keys ignored)
    assert _settings_compatible_for_reuse(existing_run, requested) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

