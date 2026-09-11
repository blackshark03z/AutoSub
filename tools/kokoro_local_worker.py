from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import wave
from pathlib import Path

SAMPLE_RATE = 24_000


class WorkerFailure(RuntimeError):
    def __init__(self, failure_class: str) -> None:
        self.failure_class = failure_class
        super().__init__(failure_class)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_wav(path: Path, samples) -> None:
    import numpy as np

    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not len(array) or not np.isfinite(array).all() or float(np.max(np.abs(array))) > 1.25:
        raise WorkerFailure("KOKORO_AUDIO_INVALID")
    pcm = (np.clip(array, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".wav", dir=path.parent)
    os.close(descriptor)
    try:
        with wave.open(temporary, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(SAMPLE_RATE)
            output.writeframes(pcm)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _map_tokens(source_text: str, result, audio_offset: float, source_cursor: int) -> tuple[list[dict], int]:
    graphemes = str(result.graphemes or "")
    result_start = source_text.find(graphemes, source_cursor) if graphemes else source_cursor
    if result_start < 0:
        raise WorkerFailure("KOKORO_ALIGNMENT_FAILED")
    result_end = result_start + len(graphemes)
    cursor = source_cursor
    mapped: list[dict] = []
    for token in result.tokens or []:
        token_text = str(token.text or "")
        if not token_text:
            continue
        token_start = source_text.find(token_text, cursor)
        if token_start < 0:
            continue
        token_end = token_start + len(token_text)
        cursor = token_end
        if token.start_ts is None or token.end_ts is None:
            continue
        start = audio_offset + float(token.start_ts)
        end = audio_offset + float(token.end_ts)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise WorkerFailure("KOKORO_ALIGNMENT_FAILED")
        mapped.append({
            "text": token_text,
            "source_start": token_start,
            "source_end": token_end,
            "start": round(start, 6),
            "end": round(end, 6),
        })
    return mapped, max(result_end, cursor)


def run(request_path: Path, result_path: Path) -> None:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    try:
        import numpy as np
        from kokoro import KModel, KPipeline

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
        voice_path = request["voice_path"]
        pipeline.load_voice(voice_path)
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
        _write_json(result_path, {
            "status": "SUCCESS",
            "identity_sha256": request["identity_sha256"],
            "sample_rate": SAMPLE_RATE,
            "duration_seconds": round(len(audio) / SAMPLE_RATE, 6),
            "frame_count": len(audio),
            "tokens": tokens,
        })
    except WorkerFailure as exc:
        _write_json(result_path, {"status": "FAILED", "failure_class": exc.failure_class})
        raise
    except Exception as exc:
        _write_json(result_path, {"status": "FAILED", "failure_class": "KOKORO_SYNTHESIS_FAILED"})
        raise WorkerFailure("KOKORO_SYNTHESIS_FAILED") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    run(args.request, args.result)


if __name__ == "__main__":
    main()
