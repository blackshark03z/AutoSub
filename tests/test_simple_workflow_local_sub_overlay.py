import json
import subprocess
from pathlib import Path
import pytest

from app.db.session import init_db, session_scope
from app.services.simple_workflow import (
    create_or_reuse_run,
    _bounded_subtitle_process,
    _get_run,
    _completed_result_validation,
)
from app.services.clean_subtitle_render import _ffmpeg_filter_path
from app.services.subtitle_tracks import create_provider_translation_track, resolved_cues


@pytest.fixture
def video_with_chinese_hardsub(tmp_path: Path) -> Path:
    """Fixture video with AAC audio and burned-in Chinese subtitle at bottom."""
    ass_content = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,SimSun,34,&H00FFFFFF,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,55,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.50,0:00:02.50,Default,,0,0,0,,陈长安携神兽麒麟重返家族
"""
    ass_path = tmp_path / "source_chinese.ass"
    ass_path.write_text(ass_content, encoding="utf-8")
    escaped_ass = _ffmpeg_filter_path(ass_path)

    vid_path = tmp_path / "video_chinese_sub.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=0x282828:s=1280x720:d=3.0:r=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3.0",
        "-vf", f"subtitles='{escaped_ass}'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(vid_path),
    ]
    subprocess.run(cmd, check=True)
    return vid_path


def test_main_workflow_wires_local_sub_mask_overlay(video_with_chinese_hardsub: Path):
    """P0 Acceptance: Simple workflow called with LOCAL_SUB_MASK_OVERLAY configuration
    passes mode to build_source_replacement_plan, creates local drawbox and Vietnamese ASS,
    forbids delogo, and records actual mode in render manifest and run manifest.
    """
    init_db()
    
    settings = {
        "render_mode": "LOCAL_SUB_MASK_OVERLAY",
        "target_language": "Vietnamese",
    }
    run_payload = create_or_reuse_run(str(video_with_chinese_hardsub), settings=settings)
    run_id = run_payload["run_id"]
    run_dir = Path(run_payload["layout"]["work"]).parent

    cues = [
        {
            "cue_id": "CUE_0001",
            "start_ms": 500,
            "end_ms": 2500,
            "text": "陈长安携神兽麒麟重返家族",
            "resolved_text": "Trần Trường An về gia tộc",
        }
    ]
    create_provider_translation_track(
        run_id,
        source_cues=cues,
        translated_texts=["Trần Trường An về gia tộc"],
        metadata={"translation_provider": "offline", "target_language": "Vietnamese"},
    )
    resolved = resolved_cues(run_id)

    with session_scope() as session:
        row = _get_run(session, run_id)
        _bounded_subtitle_process(row, resolved)
        
        # 1. Output video exists and is not empty
        output_file = run_dir / "output" / "final_video.mp4"
        assert output_file.exists()
        assert output_file.stat().st_size > 0
        
        # 2. Render manifest records LOCAL_SUB_MASK_OVERLAY and Vietnamese
        subtitles_dir = run_dir / "subtitles"
        render_manifest_file = subtitles_dir / "render_manifest.json"
        assert render_manifest_file.exists()
        render_manifest = json.loads(render_manifest_file.read_text(encoding="utf-8"))
        assert render_manifest["render_mode"] == "LOCAL_SUB_MASK_OVERLAY"
        assert render_manifest["target_language"] == "Vietnamese"
        assert render_manifest["filter"] == "LOCAL_SUB_MASK_OVERLAY"
        
        # 3. Render plan records mode and Vietnamese language
        render_plan_file = subtitles_dir / "render_plan.json"
        assert render_plan_file.exists()
        render_plan = json.loads(render_plan_file.read_text(encoding="utf-8"))
        assert render_plan["render_mode"] == "LOCAL_SUB_MASK_OVERLAY"
        assert render_plan["target_language"] == "Vietnamese"
        
        # 4. Filter script has drawbox and strictly NO delogo
        filter_script = (subtitles_dir / "ffmpeg_filter_script.txt").read_text(encoding="utf-8")
        assert "drawbox=" in filter_script
        assert "delogo=" not in filter_script
        assert "dialogue_subtitles_vi.ass" in filter_script
        
        # 5. ASS file exists with Vietnamese Dialogue text
        ass_vi = subtitles_dir / "dialogue_subtitles_vi.ass"
        assert ass_vi.exists()
        ass_text = ass_vi.read_text(encoding="utf-8")
        assert "Trần Trường An" in ass_text
        
        # 6. Run manifest records actual mode
        run_manifest_file = run_dir / "run_manifest.json"
        assert run_manifest_file.exists()
        run_manifest = json.loads(run_manifest_file.read_text(encoding="utf-8"))
        assert run_manifest["render_mode"] == "LOCAL_SUB_MASK_OVERLAY"
        assert run_manifest["target_language"] == "Vietnamese"


def test_main_workflow_needs_review_blocks_render(video_with_chinese_hardsub: Path):
    """P0 Acceptance: If Vietnamese translation cannot fit inside the source subtitle region
    without overflowing the tight mask, the layout returns NEEDS_REVIEW,
    _bounded_subtitle_process raises SubtitleLayoutNeedsReviewError with SUBTITLE_LAYOUT_NEEDS_REVIEW,
    writes exact cue evidence, and NEVER produces final_video.mp4.
    """
    from app.services.simple_workflow import SubtitleLayoutNeedsReviewError, start_processing

    init_db()
    
    settings = {
        "render_mode": "LOCAL_SUB_MASK_OVERLAY",
        "target_language": "Vietnamese",
    }
    run_payload = create_or_reuse_run(str(video_with_chinese_hardsub), settings=settings)
    run_id = run_payload["run_id"]
    run_dir = Path(run_payload["layout"]["work"]).parent

    # Long Vietnamese text overflowing the 1-line 34px source bbox
    cues = [
        {
            "cue_id": "CUE_LONG_0001",
            "start_ms": 500,
            "end_ms": 2500,
            "text": "陈长安携神兽麒麟重返家族",
            "resolved_text": "Trần Trường An mang theo Thượng Cổ Thần Thú Kỳ Lân trở về Trần gia sau mười năm xa cách",
        }
    ]
    create_provider_translation_track(
        run_id,
        source_cues=cues,
        translated_texts=["Trần Trường An mang theo Thượng Cổ Thần Thú Kỳ Lân trở về Trần gia sau mười năm xa cách"],
        metadata={"translation_provider": "offline", "target_language": "Vietnamese"},
    )
    resolved = resolved_cues(run_id)

    with session_scope() as session:
        row = _get_run(session, run_id)
        with pytest.raises(SubtitleLayoutNeedsReviewError) as exc_info:
            _bounded_subtitle_process(row, resolved)
        
        assert "SUBTITLE_LAYOUT_NEEDS_REVIEW" in str(exc_info.value)
        assert exc_info.value.code == "SUBTITLE_LAYOUT_NEEDS_REVIEW"
        assert len(exc_info.value.review_details) == 1
        detail = exc_info.value.review_details[0]
        assert detail["cue_id"] == "CUE_LONG_0001"
        assert "translation_exceeds" in detail["review_reason"]
        assert detail["source_bbox"] is not None
        assert detail["text_dimensions"]["width"] > 0
        assert detail["layout_limits"]["mask"] is not None

    # Verify evidence file is written
    subtitles_dir = run_dir / "subtitles"
    evidence_file = subtitles_dir / "subtitle_layout_needs_review.json"
    assert evidence_file.exists()
    evidence_data = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert len(evidence_data) == 1
    assert evidence_data[0]["cue_id"] == "CUE_LONG_0001"

    # Verify output video was NOT completed or published
    output_file = run_dir / "output" / "final_video.mp4"
    assert not output_file.exists()

    # Now verify end-to-end start_processing marks run as blocked with SUBTITLE_LAYOUT_NEEDS_REVIEW
    with pytest.raises(SubtitleLayoutNeedsReviewError):
        start_processing(run_id)

    with session_scope() as session:
        row_after = _get_run(session, run_id)
        assert row_after.internal_state == "blocked"
        assert row_after.failure_category == "SUBTITLE_LAYOUT_NEEDS_REVIEW"
        assert row_after.output_path is None


def test_main_workflow_legacy_english_compatibility(video_with_chinese_hardsub: Path):
    """P0 Compatibility: Default English workflow continues to use legacy mode
    without regression outside the Pilot scope.
    """
    init_db()
    
    settings = {
        "target_language": "English",
        "render_mode": "legacy",
    }
    run_payload = create_or_reuse_run(str(video_with_chinese_hardsub), settings=settings)
    run_id = run_payload["run_id"]
    run_dir = Path(run_payload["layout"]["work"]).parent

    cues = [
        {
            "cue_id": "CUE_0001",
            "start_ms": 500,
            "end_ms": 2500,
            "text": "陈长安携神兽麒麟重返家族",
            "resolved_text": "Chen Chang'an returns to the family with Qilin",
        }
    ]
    create_provider_translation_track(
        run_id,
        source_cues=cues,
        translated_texts=["Chen Chang'an returns to the family with Qilin"],
        metadata={"translation_provider": "offline", "target_language": "English"},
    )
    resolved = resolved_cues(run_id)

    with session_scope() as session:
        row = _get_run(session, run_id)
        _bounded_subtitle_process(row, resolved)
        
        subtitles_dir = run_dir / "subtitles"
        filter_script = (subtitles_dir / "ffmpeg_filter_script.txt").read_text(encoding="utf-8")
        
        # Legacy mode uses delogo and lower-third presentation plate
        assert "delogo=" in filter_script
        assert "drawbox=" in filter_script
        
        render_plan = json.loads((subtitles_dir / "render_plan.json").read_text(encoding="utf-8"))
        assert render_plan["render_mode"] == "legacy"
        assert render_plan["target_language"] == "English"
