from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.services.localization_run_config import MAX_KOKORO_SPEED, MIN_KOKORO_SPEED

KOKORO_MODEL_REPO = "hexgrad/Kokoro-82M"
KOKORO_SAMPLE_RATE = 24_000
KOKORO_PREVIEW_TEXT = "This is the voice AutoSub will use for your video."
KOKORO_RUNTIME_ENV = "AUTOSUB_KOKORO_RUNTIME"
KOKORO_MODEL_CACHE_ENV = "AUTOSUB_KOKORO_MODEL_CACHE"


class KokoroLocalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class KokoroRuntime:
    root: Path
    python: Path
    package_version: str
    model_cache: Path
    model_snapshot: str
    config_path: Path
    model_path: Path
    voices_dir: Path


_VOICES = (
    ("af_alloy", "Alloy", "American English", "female", "a"),
    ("af_aoede", "Aoede", "American English", "female", "a"),
    ("af_bella", "Bella", "American English", "female", "a"),
    ("af_heart", "Heart", "American English", "female", "a"),
    ("af_jessica", "Jessica", "American English", "female", "a"),
    ("af_kore", "Kore", "American English", "female", "a"),
    ("af_nicole", "Nicole", "American English", "female", "a"),
    ("af_nova", "Nova", "American English", "female", "a"),
    ("af_river", "River", "American English", "female", "a"),
    ("af_sarah", "Sarah", "American English", "female", "a"),
    ("af_sky", "Sky", "American English", "female", "a"),
    ("am_adam", "Adam", "American English", "male", "a"),
    ("am_echo", "Echo", "American English", "male", "a"),
    ("am_eric", "Eric", "American English", "male", "a"),
    ("am_fenrir", "Fenrir", "American English", "male", "a"),
    ("am_liam", "Liam", "American English", "male", "a"),
    ("am_michael", "Michael", "American English", "male", "a"),
    ("am_onyx", "Onyx", "American English", "male", "a"),
    ("am_puck", "Puck", "American English", "male", "a"),
    ("am_santa", "Santa", "American English", "male", "a"),
    ("bf_alice", "Alice", "British English", "female", "b"),
    ("bf_emma", "Emma", "British English", "female", "b"),
    ("bf_isabella", "Isabella", "British English", "female", "b"),
    ("bf_lily", "Lily", "British English", "female", "b"),
    ("bm_daniel", "Daniel", "British English", "male", "b"),
    ("bm_fable", "Fable", "British English", "male", "b"),
    ("bm_george", "George", "British English", "male", "b"),
    ("bm_lewis", "Lewis", "British English", "male", "b"),
)
VOICE_INDEX = {voice_id: (name, accent, gender, language) for voice_id, name, accent, gender, language in _VOICES}


def _package_version(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            return match.group(1)
    command = [str(root / ".venv" / "Scripts" / "python.exe"), "-c", "import importlib.metadata; print(importlib.metadata.version('kokoro'))"]
    try:
        return subprocess.check_output(command, cwd=root, text=True, stderr=subprocess.DEVNULL, timeout=20).strip()
    except Exception as exc:
        raise KokoroLocalError("KOKORO_RUNTIME_NOT_FOUND", "Kokoro package version could not be verified") from exc


def _default_model_cache() -> Path:
    configured = os.environ.get(KOKORO_MODEL_CACHE_ENV)
    if configured:
        return Path(configured).expanduser()
    preferred = Path(r"D:\kokoro-models\huggingface\hub\models--hexgrad--Kokoro-82M")
    if preferred.exists():
        return preferred
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return hf_home / "hub" / "models--hexgrad--Kokoro-82M"


def discover_kokoro_runtime() -> KokoroRuntime:
    root = Path(os.environ.get(KOKORO_RUNTIME_ENV, r"D:\kokoro")).expanduser().resolve()
    python = root / ".venv" / "Scripts" / "python.exe"
    if not root.is_dir() or not python.is_file():
        raise KokoroLocalError("KOKORO_RUNTIME_NOT_FOUND", "Kokoro runtime is not installed")
    model_cache = _default_model_cache().resolve()
    try:
        snapshot = (model_cache / "refs" / "main").read_text(encoding="utf-8").strip()
    except OSError:
        snapshots = sorted((model_cache / "snapshots").glob("*")) if (model_cache / "snapshots").is_dir() else []
        snapshot = snapshots[-1].name if snapshots else ""
    if not re.fullmatch(r"[0-9a-f]{40}", snapshot):
        raise KokoroLocalError("KOKORO_MODEL_NOT_FOUND", "Kokoro model snapshot is unavailable")
    snapshot_root = model_cache / "snapshots" / snapshot
    config_path = snapshot_root / "config.json"
    model_path = snapshot_root / "kokoro-v1_0.pth"
    voices_dir = snapshot_root / "voices"
    if not config_path.is_file() or not model_path.is_file() or not voices_dir.is_dir():
        raise KokoroLocalError("KOKORO_MODEL_NOT_FOUND", "Kokoro model files are incomplete")
    return KokoroRuntime(
        root=root,
        python=python,
        package_version=_package_version(root),
        model_cache=model_cache,
        model_snapshot=snapshot,
        config_path=config_path,
        model_path=model_path,
        voices_dir=voices_dir,
    )


def list_kokoro_voices() -> list[dict[str, Any]]:
    try:
        runtime = discover_kokoro_runtime()
    except KokoroLocalError:
        runtime = None
    voices = []
    for voice_id, name, accent, gender, language in _VOICES:
        voice_path = runtime.voices_dir / f"{voice_id}.pt" if runtime else None
        voices.append({
            "voice_id": voice_id,
            "name": name,
            "accent": accent,
            "gender": gender,
            "language_code": language,
            "cached": bool(voice_path and voice_path.is_file() and voice_path.stat().st_size > 0),
        })
    return voices


def kokoro_status() -> dict[str, Any]:
    try:
        runtime = discover_kokoro_runtime()
    except KokoroLocalError as exc:
        return {"ready": False, "provider": "kokoro_local", "error_code": exc.code, "message": str(exc), "voices": list_kokoro_voices()}
    cached = [item["voice_id"] for item in list_kokoro_voices() if item["cached"]]
    return {
        "ready": True,
        "provider": "kokoro_local",
        "model": KOKORO_MODEL_REPO,
        "model_snapshot": runtime.model_snapshot,
        "runtime_version": runtime.package_version,
        "sample_rate": KOKORO_SAMPLE_RATE,
        "cached_voice_count": len(cached),
        "cached_voices": cached,
        "voices": list_kokoro_voices(),
    }


def _preview_identity(runtime: KokoroRuntime, voice_id: str, speed: float, text: str) -> str:
    payload = {
        "provider": "kokoro_local",
        "model": KOKORO_MODEL_REPO,
        "model_snapshot": runtime.model_snapshot,
        "voice_id": voice_id,
        "speed": round(float(speed), 3),
        "text": text,
        "worker_version": 1,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def kokoro_preview_audio_path(identity: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", str(identity or "")):
        raise KokoroLocalError("KOKORO_PREVIEW_NOT_FOUND", "Kokoro preview was not found")
    cache_dir = get_settings().data_dir / "cache" / "kokoro_preview"
    wav_path = (cache_dir / f"{identity}.wav").resolve()
    sidecar_path = (cache_dir / f"{identity}.json").resolve()
    if not wav_path.is_relative_to(cache_dir.resolve()) or not sidecar_path.is_relative_to(cache_dir.resolve()):
        raise KokoroLocalError("KOKORO_PREVIEW_NOT_FOUND", "Kokoro preview was not found")
    if not wav_path.is_file() or not sidecar_path.is_file():
        raise KokoroLocalError("KOKORO_PREVIEW_NOT_FOUND", "Kokoro preview was not found")
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KokoroLocalError("KOKORO_PREVIEW_NOT_FOUND", "Kokoro preview metadata is invalid") from exc
    if sidecar.get("identity_sha256") != identity or sidecar.get("audio_sha256") != sha256_file(wav_path):
        raise KokoroLocalError("KOKORO_PREVIEW_NOT_FOUND", "Kokoro preview integrity check failed")
    return wav_path


def ensure_kokoro_voice_cached(voice_id: str) -> Path:
    if voice_id not in VOICE_INDEX:
        raise KokoroLocalError("KOKORO_VOICE_INVALID", "Unknown Kokoro voice")
    runtime = discover_kokoro_runtime()
    voice_path = runtime.voices_dir / f"{voice_id}.pt"
    if voice_path.is_file() and voice_path.stat().st_size > 0:
        return voice_path
    code = (
        "from huggingface_hub import hf_hub_download; "
        "print(hf_hub_download(repo_id='hexgrad/Kokoro-82M', "
        f"filename='voices/{voice_id}.pt', revision='{runtime.model_snapshot}', "
        f"cache_dir=r'{runtime.model_cache.parent}'))"
    )
    env = dict(os.environ)
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [str(runtime.python), "-c", code],
        cwd=runtime.root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise KokoroLocalError("KOKORO_VOICE_PREPARE_FAILED", "Selected Kokoro voice could not be prepared")
    runtime = discover_kokoro_runtime()
    voice_path = runtime.voices_dir / f"{voice_id}.pt"
    if not voice_path.is_file() or voice_path.stat().st_size <= 0:
        raise KokoroLocalError("KOKORO_VOICE_PREPARE_FAILED", "Selected Kokoro voice was not found after preparation")
    return voice_path


def synthesize_kokoro_to_file(
    voice_id: str,
    speed: float,
    text: str,
    output_path: Path,
) -> dict[str, Any]:
    runtime = discover_kokoro_runtime()
    if voice_id not in VOICE_INDEX:
        raise KokoroLocalError("KOKORO_VOICE_INVALID", "Unknown Kokoro voice")
    try:
        speed = float(speed)
    except (TypeError, ValueError) as exc:
        raise KokoroLocalError("KOKORO_SPEED_INVALID", "Voice speed must be a number") from exc
    if speed < MIN_KOKORO_SPEED or speed > MAX_KOKORO_SPEED:
        raise KokoroLocalError("KOKORO_SPEED_INVALID", f"Voice speed must be between {MIN_KOKORO_SPEED:.2f} and {MAX_KOKORO_SPEED:.2f}")
    text = str(text or "").strip()
    if not text:
        raise KokoroLocalError("KOKORO_TEXT_INVALID", "Kokoro text cannot be empty")
    voice_path = ensure_kokoro_voice_cached(voice_id)
    language = VOICE_INDEX[voice_id][3]
    identity = _preview_identity(runtime, voice_id, speed, text)
    data_root = get_settings().data_dir.resolve()
    output_path = output_path.resolve()
    if not output_path.is_relative_to(data_root):
        raise KokoroLocalError("KOKORO_OUTPUT_INVALID", "Kokoro output must stay inside the AutoSub data directory")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = {
        "model_repo": KOKORO_MODEL_REPO,
        "config_path": str(runtime.config_path),
        "model_path": str(runtime.model_path),
        "voice_path": str(voice_path),
        "voice_id": voice_id,
        "language": language,
        "speed": speed,
        "text": text,
        "output_path": str(output_path),
        "identity_sha256": identity,
    }
    with tempfile.TemporaryDirectory(prefix="autosub_kokoro_") as temporary:
        request_path = Path(temporary) / "request.json"
        result_path = Path(temporary) / "result.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_CACHE": str(runtime.model_cache.parent),
            "PYTHONIOENCODING": "utf-8",
        })
        worker = get_settings().root / "tools" / "kokoro_local_worker.py"
        completed = subprocess.run(
            [str(runtime.python), str(worker), "--request", str(request_path), "--result", str(result_path)],
            cwd=runtime.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            check=False,
        )
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KokoroLocalError("KOKORO_SYNTHESIS_FAILED", completed.stderr[-500:] or "Kokoro synthesis failed") from exc
        if completed.returncode != 0 or result.get("status") != "SUCCESS":
            raise KokoroLocalError(str(result.get("failure_class") or "KOKORO_SYNTHESIS_FAILED"), "Kokoro synthesis failed")
    if not output_path.is_file() or output_path.stat().st_size <= 44:
        raise KokoroLocalError("KOKORO_AUDIO_INVALID", "Kokoro did not produce valid audio")
    alignment = {
        "identity_sha256": identity,
        "audio_sha256": sha256_file(output_path),
        "provider": "kokoro_local",
        "model": KOKORO_MODEL_REPO,
        "model_snapshot": runtime.model_snapshot,
        "voice_id": voice_id,
        "speed": speed,
        "sample_rate": result.get("sample_rate"),
        "duration_seconds": result.get("duration_seconds"),
        "tokens": result.get("tokens") or [],
    }
    alignment_path = output_path.with_suffix(output_path.suffix + ".alignment.json")
    alignment_path.write_text(json.dumps(alignment, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**alignment, "audio_path": str(output_path), "alignment_path": str(alignment_path)}


def synthesize_kokoro_preview(
    voice_id: str,
    speed: float,
    *,
    text: str = KOKORO_PREVIEW_TEXT,
) -> dict[str, Any]:
    runtime = discover_kokoro_runtime()
    if voice_id not in VOICE_INDEX:
        raise KokoroLocalError("KOKORO_VOICE_INVALID", "Unknown Kokoro voice")
    try:
        speed = float(speed)
    except (TypeError, ValueError) as exc:
        raise KokoroLocalError("KOKORO_SPEED_INVALID", "Voice speed must be a number") from exc
    if speed < MIN_KOKORO_SPEED or speed > MAX_KOKORO_SPEED:
        raise KokoroLocalError("KOKORO_SPEED_INVALID", f"Voice speed must be between {MIN_KOKORO_SPEED:.2f} and {MAX_KOKORO_SPEED:.2f}")
    voice_path = ensure_kokoro_voice_cached(voice_id)
    language = VOICE_INDEX[voice_id][3]
    identity = _preview_identity(runtime, voice_id, speed, text)
    cache_dir = get_settings().data_dir / "cache" / "kokoro_preview"
    cache_dir.mkdir(parents=True, exist_ok=True)
    wav_path = cache_dir / f"{identity}.wav"
    sidecar_path = cache_dir / f"{identity}.json"
    if wav_path.is_file() and sidecar_path.is_file():
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if sidecar.get("identity_sha256") == identity and sidecar.get("audio_sha256") == sha256_file(wav_path):
                return {**sidecar, "audio_path": str(wav_path.resolve()), "cache_status": "hit"}
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    request = {
        "model_repo": KOKORO_MODEL_REPO,
        "config_path": str(runtime.config_path),
        "model_path": str(runtime.model_path),
        "voice_path": str(voice_path),
        "voice_id": voice_id,
        "language": language,
        "speed": speed,
        "text": text,
        "output_path": str(wav_path),
        "identity_sha256": identity,
    }
    with tempfile.TemporaryDirectory(prefix="autosub_kokoro_") as temporary:
        request_path = Path(temporary) / "request.json"
        result_path = Path(temporary) / "result.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_CACHE": str(runtime.model_cache.parent),
            "PYTHONIOENCODING": "utf-8",
        })
        worker = get_settings().root / "tools" / "kokoro_local_worker.py"
        completed = subprocess.run(
            [str(runtime.python), str(worker), "--request", str(request_path), "--result", str(result_path)],
            cwd=runtime.root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            check=False,
        )
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise KokoroLocalError("KOKORO_SYNTHESIS_FAILED", completed.stderr[-500:] or "Kokoro preview failed") from exc
        if completed.returncode != 0 or result.get("status") != "SUCCESS":
            raise KokoroLocalError(str(result.get("failure_class") or "KOKORO_SYNTHESIS_FAILED"), "Kokoro preview failed")
    if not wav_path.is_file():
        raise KokoroLocalError("KOKORO_AUDIO_INVALID", "Kokoro did not produce preview audio")
    sidecar = {
        "identity_sha256": identity,
        "audio_sha256": sha256_file(wav_path),
        "provider": "kokoro_local",
        "model": KOKORO_MODEL_REPO,
        "model_snapshot": runtime.model_snapshot,
        "voice_id": voice_id,
        "speed": speed,
        "sample_rate": result.get("sample_rate"),
        "duration_seconds": result.get("duration_seconds"),
        "tokens": result.get("tokens") or [],
    }
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**sidecar, "audio_path": str(wav_path.resolve()), "cache_status": "miss"}
