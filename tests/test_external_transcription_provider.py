import json
import subprocess
from pathlib import Path

import pytest

from app.providers.asr.autosubs_provider import (
    AUTOSUBS_MAX_TIMEOUT_SECONDS,
    AUTOSUBS_SECONDS_PER_AUDIO_SECOND,
    AUTOSUBS_VERSION,
    AutoSubsASRProvider,
    AutoSubsConfig,
    AutoSubsRuntimeError,
)


def _provider(tmp_path: Path) -> tuple[AutoSubsASRProvider, Path]:
    binary = tmp_path / "autosubs.exe"
    binary.write_bytes(b"fixture")
    audio = tmp_path / "source.wav"
    audio.write_bytes(b"fixture")
    return AutoSubsASRProvider(AutoSubsConfig(binary, timeout_seconds=5)), audio


def test_normalizes_autosubs_timestamped_chinese_source(monkeypatch, tmp_path):
    provider, audio = _provider(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, f"autosubs {AUTOSUBS_VERSION}", "")
        if command[-1] == "--list-models":
            return subprocess.CompletedProcess(command, 0, "tiny\nbase\nsmall\n", "")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"language": "zh", "segments": [{"start": 0.05, "end": 1.25, "text": "你好，世界。"}]}),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    segments = provider.transcribe(audio, language="zh")

    assert [(segment.start, segment.end, segment.text) for segment in segments] == [(0.05, 1.25, "你好，世界。")]
    assert provider.last_metadata["provider"] == "autosubs"
    assert provider.last_metadata["language"] == "zh"
    assert provider.last_metadata["model_cache_preflight"] == "passed"
    assert "--forced-alignment" not in calls[-1]
    assert "translate" not in calls[-1]


def test_preserves_source_cues_without_semantic_mutation(monkeypatch, tmp_path):
    provider, audio = _provider(tmp_path)
    source = "  中文原文，不应被翻译。  "

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, f"autosubs {AUTOSUBS_VERSION}", "")
        if command[-1] == "--list-models":
            return subprocess.CompletedProcess(command, 0, "small\n", "")
        return subprocess.CompletedProcess(command, 0, json.dumps({"language": "zh", "segments": [{"start": 1, "end": 2, "text": source}]}), "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert provider.transcribe(audio, language="zh")[0].text == source


@pytest.mark.parametrize(
    ("stdout", "message"),
    [
        ("not-json", "malformed JSON"),
        (json.dumps({"segments": [{"start": 2, "end": 1, "text": "中文"}]}), "invalid timestamps"),
        (json.dumps({"segments": []}), "no usable timestamped source cues"),
    ],
)
def test_invalid_engine_output_is_actionable(monkeypatch, tmp_path, stdout, message):
    provider, audio = _provider(tmp_path)

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, f"autosubs {AUTOSUBS_VERSION}", "")
        if command[-1] == "--list-models":
            return subprocess.CompletedProcess(command, 0, "small\n", "")
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(AutoSubsRuntimeError, match=message):
        provider.transcribe(audio)


def test_preflight_rejects_missing_model_from_the_real_engine_listing(monkeypatch, tmp_path):
    binary = tmp_path / "autosubs.exe"
    binary.write_bytes(b"fixture")
    provider = AutoSubsASRProvider(AutoSubsConfig(binary))

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, f"autosubs {AUTOSUBS_VERSION}", "")
        return subprocess.CompletedProcess(command, 0, "tiny\nbase\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(AutoSubsRuntimeError, match="model 'small' is not cached"):
        provider.preflight()


def test_preflight_rejects_substring_version_match(monkeypatch, tmp_path):
    provider, _audio = _provider(tmp_path)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "autosubs 13.8.0", ""),
    )
    with pytest.raises(AutoSubsRuntimeError, match=f"v{AUTOSUBS_VERSION} is required"):
        provider.preflight()


def test_engine_translation_is_prohibited(tmp_path):
    provider, audio = _provider(tmp_path)
    with pytest.raises(AutoSubsRuntimeError, match="task=transcribe"):
        provider.transcribe(audio, task="translate")


def test_duration_aware_timeout_scales_full_wav_but_stays_bounded(monkeypatch, tmp_path):
    provider, audio = _provider(tmp_path)

    monkeypatch.setattr(
        "app.providers.asr.autosubs_provider._wav_duration_seconds",
        lambda _path: 366.296188,
    )

    assert provider._transcription_timeout_seconds(audio) == 1283

    monkeypatch.setattr(
        "app.providers.asr.autosubs_provider._wav_duration_seconds",
        lambda _path: 99999.0,
    )
    assert provider._transcription_timeout_seconds(audio) == AUTOSUBS_MAX_TIMEOUT_SECONDS


def test_duration_aware_timeout_is_passed_to_autosubs(monkeypatch, tmp_path):
    provider, audio = _provider(tmp_path)
    observed_timeouts = []

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, f"autosubs {AUTOSUBS_VERSION}", "")
        if command[-1] == "--list-models":
            return subprocess.CompletedProcess(command, 0, "small\n", "")
        observed_timeouts.append(kwargs["timeout"])
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"language": "zh", "segments": [{"start": 0, "end": 1, "text": "source"}]}),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "app.providers.asr.autosubs_provider._wav_duration_seconds",
        lambda _path: 366.296188,
    )

    provider.transcribe(audio)

    assert observed_timeouts == [1283]
    assert AUTOSUBS_SECONDS_PER_AUDIO_SECOND > 2.762


def test_prefers_autosubs_original_source_segments_over_resegmented_output(monkeypatch, tmp_path):
    provider, audio = _provider(tmp_path)
    original = {"start": 1, "end": 2, "text": "\u4e2d\u6587\u539f\u6587\u3002"}

    def fake_run(command, **kwargs):
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 0, f"autosubs {AUTOSUBS_VERSION}", "")
        if command[-1] == "--list-models":
            return subprocess.CompletedProcess(command, 0, "small\n", "")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "language": "zh",
                    "segments": [{"start": 1.5, "end": 1.5, "text": "presentation-only artifact"}],
                    "originalSegments": [original],
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    segments = provider.transcribe(audio)

    assert [(segment.start, segment.end, segment.text) for segment in segments] == [(1.0, 2.0, original["text"])]
    assert provider.last_metadata["source_segment_field"] == "originalSegments"
    assert provider.last_metadata["raw_segment_count"] == 1
