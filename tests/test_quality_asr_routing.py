from __future__ import annotations

import json
from pathlib import Path

import app.services.simple_workflow as workflow
from app.providers.asr.compute_whisper_provider import ComputeWhisperASRProvider
from app.providers.compute.base import ComputeProviderCapabilities
from app.services.subtitle_tracks import SubtitleContentUnavailableError


class _Modal:
    def __init__(self, *, no_overage: bool):
        self.no_overage = no_overage

    def capabilities(self):
        return ComputeProviderCapabilities(
            provider="modal",
            automated=True,
            commercial_use_allowed=True,
            staged_artifacts=True,
            resumable=True,
            no_paid_overage_verified=self.no_overage,
            available=True,
        )


def _requested():
    return {
        "localization_mode": "full_visual_localization",
        "compute_policy": "auto",
        "target_language": "English",
    }


def test_long_v2_asr_uses_cloud_only_when_no_overage_is_verified(tmp_path: Path, monkeypatch) -> None:
    modal = _Modal(no_overage=True)
    monkeypatch.setattr(workflow, "discover_modal_compute_config", lambda _root: object())
    monkeypatch.setattr(workflow, "ModalComputeProvider", lambda _config: modal)
    calls = []

    def run(_run_id, **kwargs):
        calls.append(kwargs)
        provider_factory = kwargs.get("provider_factory")
        assert provider_factory is not None
        assert isinstance(provider_factory(), ComputeWhisperASRProvider)
        return {"status": "PASS", "metadata": {"asr_provider": "whisper_compute"}}

    monkeypatch.setattr(workflow, "ensure_external_transcription_track", run)
    result = workflow._ensure_external_transcription_with_quality_route(
        "run-1",
        source_path=tmp_path / "source.mp4",
        run_directory=tmp_path / "run",
        source_duration_seconds=458.0,
        target_language="English",
        source_language="zh",
        requested=_requested(),
    )
    assert result["metadata"]["asr_provider"] == "whisper_compute"
    assert len(calls) == 1
    route = json.loads((tmp_path / "run" / "logs" / "asr_compute_route.json").read_text(encoding="utf-8"))
    assert route["route"] == "cloud"
    assert route["no_paid_overage_verified"] is True


def test_unverified_no_overage_forces_local_without_constructing_cloud_asr(tmp_path: Path, monkeypatch) -> None:
    modal = _Modal(no_overage=False)
    monkeypatch.setattr(workflow, "discover_modal_compute_config", lambda _root: object())
    monkeypatch.setattr(workflow, "ModalComputeProvider", lambda _config: modal)
    calls = []

    def run(_run_id, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("provider_factory") is None
        return {"status": "PASS", "metadata": {"asr_provider": "autosubs"}}

    monkeypatch.setattr(workflow, "ensure_external_transcription_track", run)
    workflow._ensure_external_transcription_with_quality_route(
        "run-2",
        source_path=tmp_path / "source.mp4",
        run_directory=tmp_path / "run",
        source_duration_seconds=458.0,
        target_language="English",
        source_language="zh",
        requested=_requested(),
    )
    assert len(calls) == 1
    route = json.loads((tmp_path / "run" / "logs" / "asr_compute_route.json").read_text(encoding="utf-8"))
    assert route["route"] == "local"
    assert route["reason"] == "no_qualified_cloud_provider"


def test_cloud_asr_failure_falls_back_locally_and_records_downgrade(tmp_path: Path, monkeypatch) -> None:
    modal = _Modal(no_overage=True)
    monkeypatch.setattr(workflow, "discover_modal_compute_config", lambda _root: object())
    monkeypatch.setattr(workflow, "ModalComputeProvider", lambda _config: modal)
    attempts = []

    def run(_run_id, **kwargs):
        attempts.append(bool(kwargs.get("provider_factory")))
        if kwargs.get("provider_factory"):
            raise SubtitleContentUnavailableError("cloud unavailable")
        return {"status": "PASS", "metadata": {"asr_provider": "autosubs"}}

    monkeypatch.setattr(workflow, "ensure_external_transcription_track", run)
    result = workflow._ensure_external_transcription_with_quality_route(
        "run-3",
        source_path=tmp_path / "source.mp4",
        run_directory=tmp_path / "run",
        source_duration_seconds=458.0,
        target_language="English",
        source_language="zh",
        requested=_requested(),
    )
    assert result["metadata"]["asr_provider"] == "autosubs"
    assert attempts == [True, False]
    route = json.loads((tmp_path / "run" / "logs" / "asr_compute_route.json").read_text(encoding="utf-8"))
    assert route["route"] == "local"
    assert route["fallback"] is True
    assert route["actual_provider"] == "autosubs"
