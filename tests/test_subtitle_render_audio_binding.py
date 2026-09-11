from pathlib import Path

from app.services.simple_workflow import _subtitle_render_command


def test_subtitle_render_preserves_source_audio_by_default(tmp_path: Path) -> None:
    command = _subtitle_render_command(tmp_path / "source.mp4", tmp_path / "sub.ass", tmp_path / "out.mp4")
    assert command.count("-i") == 1
    audio_codec = command.index("-c:a")
    assert command[audio_codec + 1] == "copy"


def test_subtitle_render_can_bind_generated_narration_audio(tmp_path: Path) -> None:
    narration = tmp_path / "narration.wav"
    command = _subtitle_render_command(
        tmp_path / "source.mp4",
        tmp_path / "sub.ass",
        tmp_path / "out.mp4",
        audio_override=narration,
    )
    assert command.count("-i") == 2
    assert ["-map", "0:v:0"] == command[command.index("-map"):command.index("-map") + 2]
    assert "1:a:0" in command
    audio_codec = command.index("-c:a")
    assert command[audio_codec + 1] == "aac"
    assert "copy" not in command[audio_codec:]
