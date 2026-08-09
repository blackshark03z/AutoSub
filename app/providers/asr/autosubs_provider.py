from __future__ import annotations

import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.providers.asr.base import ASRProvider, ASRSegment


AUTOSUBS_VERSION = "3.8.0"
AUTOSUBS_MODEL = "small"
AUTOSUBS_TIMEOUT_SECONDS = 900


class AutoSubsRuntimeError(RuntimeError):
    """A local AutoSubs installation cannot safely produce a source transcript."""


@dataclass(frozen=True)
class AutoSubsConfig:
    binary_path: Path
    model: str = AUTOSUBS_MODEL
    timeout_seconds: int = AUTOSUBS_TIMEOUT_SECONDS


def discover_autosubs_config(root: Path) -> AutoSubsConfig:
    """Locate the portable engine without requiring an operator ASR setting.

    Environment paths are an installer/integration override.  The default is
    the portable bundle location; this adapter never downloads an engine or a
    model on the user's behalf.
    """
    root = Path(root).resolve()
    binary_value = os.environ.get("TOOL_AUTO_SUB_AUTOSUBS_BINARY", "").strip()
    return AutoSubsConfig(
        binary_path=(Path(binary_value) if binary_value else root / "addons" / "autosubs" / "autosubs.exe").resolve(),
        timeout_seconds=max(5, int(os.environ.get("TOOL_AUTO_SUB_AUTOSUBS_TIMEOUT_SECONDS", AUTOSUBS_TIMEOUT_SECONDS))),
    )


class AutoSubsASRProvider(ASRProvider):
    """Subprocess-only AutoSubs v3.8.0 adapter for original-language cues."""

    def __init__(self, config: AutoSubsConfig) -> None:
        self.config = config
        self.last_metadata: dict[str, Any] = {}

    def preflight(self) -> dict[str, str]:
        binary = self.config.binary_path
        if not binary.is_file():
            raise AutoSubsRuntimeError(
                "AutoSubs v3.8.0 executable is missing. Reinstall the bundled AutoSubs add-on."
            )
        try:
            version = subprocess.run(
                [str(binary), "--version"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                env=self._environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AutoSubsRuntimeError("AutoSubs version preflight failed; verify the bundled executable is runnable.") from exc
        reported = f"{version.stdout}\n{version.stderr}".strip()
        if version.returncode != 0 or not _reports_exact_version(reported):
            raise AutoSubsRuntimeError(
                f"AutoSubs v{AUTOSUBS_VERSION} is required; the bundled executable did not report that version."
            )
        try:
            cached_models = subprocess.run(
                [str(binary), "--list-models"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
                env=self._environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AutoSubsRuntimeError("AutoSubs model-cache preflight failed; verify the bundled executable is runnable.") from exc
        available = {line.strip() for line in cached_models.stdout.splitlines() if line.strip()}
        if cached_models.returncode != 0 or self.config.model not in available:
            raise AutoSubsRuntimeError(
                f"AutoSubs approved local model '{self.config.model}' is not cached. Install the packaged model before retrying; AutoSub will not start transcription."
            )
        return {"version": AUTOSUBS_VERSION, "binary": str(binary), "model": self.config.model}

    def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        task: str = "transcribe",
    ) -> list[ASRSegment]:
        if task != "transcribe":
            raise AutoSubsRuntimeError("AutoSubs source transcription only supports task=transcribe; engine translation is prohibited.")
        audio = Path(audio_path).resolve()
        if not audio.is_file():
            raise AutoSubsRuntimeError("AutoSubs audio input is missing.")
        preflight = self.preflight()
        command = [
            str(self.config.binary_path),
            str(audio),
            "--model",
            self.config.model,
            "--lang",
            _command_language(language),
            "--no-gpu",
            "--format",
            "json",
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.config.timeout_seconds,
                env=self._environment(),
                cwd=str(self.config.binary_path.parent),
            )
        except subprocess.TimeoutExpired as exc:
            raise AutoSubsRuntimeError("AutoSubs transcription timed out; no fallback engine was used.") from exc
        except OSError as exc:
            raise AutoSubsRuntimeError("AutoSubs could not be started; verify the bundled executable and FFmpeg dependency.") from exc
        if completed.returncode != 0:
            detail = _safe_detail(completed.stderr or completed.stdout)
            raise AutoSubsRuntimeError(f"AutoSubs transcription failed (exit {completed.returncode}). {detail}")
        payload = _parse_transcript(completed.stdout)
        segments = _normalize_segments(payload)
        if not segments:
            raise AutoSubsRuntimeError("AutoSubs returned no usable timestamped source cues.")
        self.last_metadata = {
            "provider": "autosubs",
            "engine": "AutoSubs",
            "engine_version": preflight["version"],
            "model": self.config.model,
            "model_cache_preflight": "passed",
            "language": payload.get("language") or language or "unknown",
            "task": "transcribe",
            "fallback_attempts": 0,
            "raw_segment_count": len(payload["segments"]),
        }
        return segments

    def _environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
        }


def _command_language(language: str | None) -> str:
    normalized = str(language or "").strip().lower()
    return "auto" if not normalized or normalized == "auto" else normalized


def _reports_exact_version(value: str) -> bool:
    return bool(re.search(rf"(?im)^autosubs\s+{re.escape(AUTOSUBS_VERSION)}\s*$", value.strip()))


def _parse_transcript(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AutoSubsRuntimeError("AutoSubs returned malformed JSON; no transcript was accepted.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise AutoSubsRuntimeError("AutoSubs returned invalid transcript JSON: segments must be a list.")
    return payload


def _normalize_segments(payload: dict[str, Any]) -> list[ASRSegment]:
    normalized: list[ASRSegment] = []
    for index, raw in enumerate(payload["segments"], start=1):
        if not isinstance(raw, dict):
            raise AutoSubsRuntimeError(f"AutoSubs segment {index} is not an object.")
        start = _seconds(raw.get("start"), index, "start")
        end = _seconds(raw.get("end"), index, "end")
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AutoSubsRuntimeError(f"AutoSubs segment {index} has empty source text.")
        if end <= start:
            raise AutoSubsRuntimeError(f"AutoSubs segment {index} has invalid timestamps.")
        # Preserve engine text verbatim.  Translation and presentation layers own
        # any later interpretation; this provider only normalizes the schema.
        normalized.append(ASRSegment(start=start, end=end, text=text))
    return normalized


def _seconds(value: Any, index: int, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AutoSubsRuntimeError(f"AutoSubs segment {index} has non-numeric {field} timestamp.") from exc
    if not math.isfinite(number) or number < 0:
        raise AutoSubsRuntimeError(f"AutoSubs segment {index} has invalid {field} timestamp.")
    return number


def _safe_detail(value: str) -> str:
    detail = " ".join(str(value).split())
    return detail[:300] if detail else "Check the local engine log and bundled model cache."
