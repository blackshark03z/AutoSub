from __future__ import annotations

import json
import sys
from pathlib import Path

from kokoro import KModel, KPipeline

from kokoro_local_worker import SAMPLE_RATE, WorkerFailure, _map_tokens, _write_json, _write_wav


def _engine_identity(request: dict) -> tuple[str, str, str, str, str]:
    return (
        str(request["model_repo"]),
        str(request["config_path"]),
        str(request["model_path"]),
        str(request["voice_path"]),
        str(request["language"]),
    )


def _load_engine(request: dict):
    model = KModel(
        repo_id=request["model_repo"],
        config=request["config_path"],
        model=request["model_path"],
    ).to("cpu").eval()
    pipeline = KPipeline(
        lang_code=request["language"],
        repo_id=request["model_repo"],
        model=model,
        device="cpu",
    )
    voice_path = str(request["voice_path"])
    pipeline.load_voice(voice_path)
    return pipeline, voice_path


def _synthesize(request: dict, pipeline, voice_path: str) -> dict:
    import numpy as np

    text = str(request["text"])
    speed = float(request["speed"])
    arrays = []
    tokens: list[dict] = []
    audio_offset = 0.0
    source_cursor = 0
    for result in pipeline(text, voice=voice_path, speed=speed, split_pattern=None):
        if result.audio is None:
            continue
        array = result.audio.detach().cpu().numpy().astype(np.float32).reshape(-1)
        if not len(array):
            continue
        mapped, source_cursor = _map_tokens(text, result, audio_offset, source_cursor)
        tokens.extend(mapped)
        arrays.append(array)
        audio_offset += len(array) / SAMPLE_RATE
    if not arrays:
        raise WorkerFailure("KOKORO_SYNTHESIS_FAILED")
    audio = np.concatenate(arrays)
    output_path = Path(request["output_path"])
    _write_wav(output_path, audio)
    return {
        "status": "SUCCESS",
        "identity_sha256": request["identity_sha256"],
        "sample_rate": SAMPLE_RATE,
        "duration_seconds": round(len(audio) / SAMPLE_RATE, 6),
        "frame_count": len(audio),
        "tokens": tokens,
    }


def main() -> None:
    pipeline = None
    voice_path = ""
    identity = None
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            command = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if command.get("command") == "shutdown":
            break
        request_path = Path(str(command.get("request_path") or ""))
        result_path = Path(str(command.get("result_path") or ""))
        if not request_path.is_file() or not str(result_path):
            continue
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
            current_identity = _engine_identity(request)
            if pipeline is None or current_identity != identity:
                pipeline, voice_path = _load_engine(request)
                identity = current_identity
            payload = _synthesize(request, pipeline, voice_path)
            _write_json(result_path, payload)
        except WorkerFailure as exc:
            _write_json(result_path, {"status": "FAILED", "failure_class": exc.failure_class})
        except Exception:
            _write_json(result_path, {"status": "FAILED", "failure_class": "KOKORO_SYNTHESIS_FAILED"})


if __name__ == "__main__":
    main()
