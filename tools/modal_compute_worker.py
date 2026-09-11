from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
from typing import Any

import modal


APP_NAME = os.environ.get("AUTOSUB_MODAL_APP", "autosub-compute")
VOLUME_NAME = os.environ.get("AUTOSUB_MODAL_VOLUME", "autosub-compute")
WORKER_VERSION = "autosub-modal-worker-v1"
WHISPER_MODEL_REVISIONS = {
    "openai/whisper-large-v3-turbo": "41f01f3fe87f28c78e2fbf8b568835947dd65ed9",
    "openai/whisper-large-v3": "06f233fe06e710322aca913c1bc4249a0d71fce1",
}
PPOCRV6_SMALL_DET_REPO = "PaddlePaddle/PP-OCRv6_small_det"
PPOCRV6_SMALL_REC_REPO = "PaddlePaddle/PP-OCRv6_small_rec"
PPOCRV6_SMALL_DET_REVISION = "680833b36f64110aea38cc7b82d0df2f5ba4c028"
PPOCRV6_SMALL_REC_REVISION = "728113cb5889dee64b2a18d707f294a76a5d1899"
PPOCRV6_SMALL_REVISION = (
    f"det:{PPOCRV6_SMALL_DET_REVISION};rec:{PPOCRV6_SMALL_REC_REVISION};paddleocr:3.7.0"
)
MOUNT = pathlib.Path("/data")

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)

asr_image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "torch",
    "transformers",
    "accelerate",
    "soundfile",
)

ocr_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install(
        "torch",
        "torchvision",
        "paddleocr==3.7.0",
        "transformers>=5.8.0",
        "huggingface_hub",
        "pillow",
    )
    .run_commands(
        "python -c \"from huggingface_hub import snapshot_download; snapshot_download('PaddlePaddle/PP-OCRv6_small_det', revision='680833b36f64110aea38cc7b82d0df2f5ba4c028', local_dir='/opt/models/ppocrv6_small_det'); snapshot_download('PaddlePaddle/PP-OCRv6_small_rec', revision='728113cb5889dee64b2a18d707f294a76a5d1899', local_dir='/opt/models/ppocrv6_small_rec')\""
    )
    .env({"PADDLE_PDX_MODEL_SOURCE": "huggingface"})
)

LAMA_MODEL_URL = "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"
lama_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("curl", "libgl1", "libglib2.0-0")
    .pip_install("torch", "torchvision", "simple-lama-inpainting==0.1.2", "pillow")
    .run_commands(
        "mkdir -p /opt/models && curl -L --fail --retry 3 '" + LAMA_MODEL_URL + "' -o /opt/models/big-lama.pt"
    )
    .env({"LAMA_MODEL": "/opt/models/big-lama.pt"})
)


def _sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_rel_path(value: Any, *, shard_id: str, area: str) -> pathlib.PurePosixPath:
    raw = str(value or "").replace("\\", "/").lstrip("/")
    path = pathlib.PurePosixPath(raw)
    expected = pathlib.PurePosixPath("shards") / shard_id / area
    if ".." in path.parts or path == expected or expected not in path.parents:
        raise ValueError("remote path escaped shard namespace")
    return path


def _request_parts(request: dict[str, Any], workload: str) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    if not isinstance(request, dict) or request.get("provider") != "modal":
        raise ValueError("invalid compute request")
    shard = request.get("shard")
    artifacts = request.get("artifacts")
    if not isinstance(shard, dict) or not isinstance(artifacts, list) or not artifacts:
        raise ValueError("compute request is missing shard artifacts")
    shard_id = str(shard.get("shard_id") or "")
    if len(shard_id) != 64 or any(char not in "0123456789abcdef" for char in shard_id):
        raise ValueError("invalid shard id")
    if str(shard.get("workload") or "") != workload:
        raise ValueError("workload mismatch")
    return shard, artifacts, shard_id


def _verify_inputs(artifacts: list[dict[str, Any]], shard_id: str) -> list[tuple[dict[str, Any], pathlib.Path]]:
    verified: list[tuple[dict[str, Any], pathlib.Path]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            raise ValueError("invalid artifact")
        remote = _safe_rel_path(item.get("remote_path"), shard_id=shard_id, area="input")
        path = MOUNT / pathlib.Path(*remote.parts)
        declared = str(item.get("sha256") or "").lower()
        if not path.is_file() or _sha256_file(path) != declared:
            raise ValueError("remote input hash mismatch")
        verified.append((item, path))
    return verified


def _manifest_path(shard_id: str) -> pathlib.Path:
    return MOUNT / "shards" / shard_id / "output" / "result_manifest.json"


def _cached_result(shard: dict[str, Any], shard_id: str) -> dict[str, Any] | None:
    manifest_path = _manifest_path(shard_id)
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("provider") != "modal"
        or payload.get("shard_id") != shard_id
        or payload.get("workload") != shard.get("workload")
        or payload.get("model") != shard.get("model")
        or payload.get("model_revision") != shard.get("model_revision")
        or payload.get("runtime_fingerprint") != shard.get("runtime_fingerprint")
    ):
        return None
    outputs = payload.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return None
    for item in outputs:
        if not isinstance(item, dict):
            return None
        try:
            rel = _safe_rel_path(item.get("remote_path"), shard_id=shard_id, area="output")
        except ValueError:
            return None
        path = MOUNT / pathlib.Path(*rel.parts)
        if not path.is_file() or _sha256_file(path) != str(item.get("sha256") or "").lower():
            return None
    result = dict(payload)
    result["cache_hit"] = True
    return result


def _write_result(
    shard: dict[str, Any],
    shard_id: str,
    *,
    outputs: list[dict[str, Any]],
    timings: dict[str, float],
    function_name: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "provider": "modal",
        "shard_id": shard_id,
        "workload": shard["workload"],
        "model": shard["model"],
        "model_revision": shard["model_revision"],
        "runtime_fingerprint": shard["runtime_fingerprint"],
        "status": "ready",
        "cache_hit": False,
        "outputs": outputs,
        "timings": {key: round(float(value), 6) for key, value in timings.items()},
        "provenance": {
            "worker_version": WORKER_VERSION,
            "function_name": function_name,
        },
    }
    manifest_path = _manifest_path(shard_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    volume.commit()
    return payload


def _write_json_output(shard_id: str, filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    output_dir = MOUNT / "shards" / shard_id / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    digest = _sha256_file(path)
    return {
        "remote_path": f"shards/{shard_id}/output/{filename}",
        "filename": filename,
        "sha256": digest,
    }


@app.function(gpu="T4", image=asr_image, volumes={"/data": volume}, timeout=1200)
def asr_shard(request: dict[str, Any]) -> dict[str, Any]:
    import soundfile as sf
    import torch
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline

    volume.reload()
    shard, artifacts, shard_id = _request_parts(request, "asr")
    cached = _cached_result(shard, shard_id)
    if cached is not None:
        return cached
    verified = _verify_inputs(artifacts, shard_id)
    if len(verified) != 1:
        raise ValueError("ASR shard requires exactly one audio artifact")
    model_id = str(shard.get("model") or "")
    model_revision = str(shard.get("model_revision") or "")
    if model_id not in WHISPER_MODEL_REVISIONS:
        raise ValueError("unsupported ASR model")
    if model_revision != WHISPER_MODEL_REVISIONS[model_id]:
        raise ValueError("unqualified ASR model revision")

    started = time.time()
    load_started = time.time()
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_id,
        revision=model_revision,
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
        use_safetensors=True,
    )
    model.to("cuda")
    processor = AutoProcessor.from_pretrained(model_id, revision=model_revision)
    transcriber = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=torch.float16,
        device=0,
    )
    load_seconds = time.time() - load_started
    samples, sample_rate = sf.read(str(verified[0][1]), dtype="float32")
    if getattr(samples, "ndim", 1) > 1:
        samples = samples.mean(axis=1)
    infer_started = time.time()
    result = transcriber(
        {"array": samples, "sampling_rate": int(sample_rate)},
        return_timestamps=True,
        generate_kwargs={"task": "transcribe"},
    )
    infer_seconds = time.time() - infer_started
    chunks = []
    for item in list(result.get("chunks") or []):
        timestamp = item.get("timestamp") or item.get("timestamps")
        chunks.append({"text": str(item.get("text") or "").strip(), "timestamp": timestamp})
    output = _write_json_output(
        shard_id,
        "asr.json",
        {
            "model": model_id,
            "model_revision": model_revision,
            "device": torch.cuda.get_device_name(0),
            "text": str(result.get("text") or "").strip(),
            "chunks": chunks,
        },
    )
    return _write_result(
        shard,
        shard_id,
        outputs=[output],
        timings={"model_load_seconds": load_seconds, "inference_seconds": infer_seconds, "remote_seconds": time.time() - started},
        function_name="asr_shard",
    )


@app.function(gpu="T4", image=ocr_image, volumes={"/data": volume}, timeout=1200)
def ocr_shard(request: dict[str, Any]) -> dict[str, Any]:
    import torch
    from paddleocr import PaddleOCR

    volume.reload()
    shard, artifacts, shard_id = _request_parts(request, "ocr")
    cached = _cached_result(shard, shard_id)
    if cached is not None:
        return cached
    verified = _verify_inputs(artifacts, shard_id)
    model_id = str(shard.get("model") or "")
    model_revision = str(shard.get("model_revision") or "")
    if model_id != "PP-OCRv6_small":
        raise ValueError("unsupported qualified OCR model")
    if model_revision != PPOCRV6_SMALL_REVISION:
        raise ValueError("unqualified OCR model revision")
    det = "PP-OCRv6_small_det"
    rec = "PP-OCRv6_small_rec"
    det_dir = pathlib.Path("/opt/models/ppocrv6_small_det")
    rec_dir = pathlib.Path("/opt/models/ppocrv6_small_rec")
    if not det_dir.is_dir() or not rec_dir.is_dir():
        raise ValueError("pinned OCR model snapshot missing from worker image")
    started = time.time()
    load_started = time.time()
    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="gpu:0",
        engine="transformers",
        text_detection_model_name=det,
        text_detection_model_dir=str(det_dir),
        text_recognition_model_name=rec,
        text_recognition_model_dir=str(rec_dir),
    )
    load_seconds = time.time() - load_started
    frames = []
    infer_started = time.time()
    for artifact, path in verified:
        texts = []
        for result_index, result in enumerate(list(ocr.predict(str(path)))):
            json_path = path.parent / f".{path.stem}.{result_index}.ocr.json"
            try:
                result.save_to_json(save_path=str(json_path))
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            finally:
                json_path.unlink(missing_ok=True)
            data = payload.get("res", payload)
            rec_texts = data.get("rec_texts") or []
            rec_scores = data.get("rec_scores") or []
            rec_polys = data.get("rec_polys") or data.get("dt_polys") or []
            for index, text in enumerate(rec_texts):
                poly = rec_polys[index] if index < len(rec_polys) else None
                if hasattr(poly, "tolist"):
                    poly = poly.tolist()
                texts.append(
                    {
                        "text": str(text),
                        "score": float(rec_scores[index]) if index < len(rec_scores) else None,
                        "poly": poly,
                    }
                )
        frames.append({"name": str(artifact.get("logical_name") or path.name), "texts": texts})
    infer_seconds = time.time() - infer_started
    output = _write_json_output(
        shard_id,
        "ocr.json",
        {
            "runtime": "modal_ppocrv6",
            "model_version": model_id,
            "model_revision": model_revision,
            "det_revision": PPOCRV6_SMALL_DET_REVISION,
            "rec_revision": PPOCRV6_SMALL_REC_REVISION,
            "device": torch.cuda.get_device_name(0),
            "frames": frames,
        },
    )
    return _write_result(
        shard,
        shard_id,
        outputs=[output],
        timings={"model_load_seconds": load_seconds, "inference_seconds": infer_seconds, "remote_seconds": time.time() - started},
        function_name="ocr_shard",
    )


def _expanded_box(box: list[int], width: int, height: int, pad: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = (int(value) for value in box)
    return max(0, x0 - pad), max(0, y0 - pad), min(width - 1, x1 + pad), min(height - 1, y1 + pad)


@app.function(gpu="T4", image=lama_image, volumes={"/data": volume}, timeout=1200)
def inpainting_shard(request: dict[str, Any]) -> dict[str, Any]:
    import torch
    from PIL import Image, ImageDraw
    from simple_lama_inpainting import SimpleLama

    volume.reload()
    shard, artifacts, shard_id = _request_parts(request, "inpainting")
    cached = _cached_result(shard, shard_id)
    if cached is not None:
        return cached
    verified = _verify_inputs(artifacts, shard_id)
    if len(verified) != 1:
        raise ValueError("inpainting shard requires exactly one image artifact")
    if str(shard.get("model") or "").lower() not in {"big-lama", "lama", "simple-lama"}:
        raise ValueError("unsupported inpainting model")
    cases = shard.get("parameters", {}).get("cases") if isinstance(shard.get("parameters"), dict) else None
    if not isinstance(cases, list) or not cases:
        raise ValueError("inpainting shard requires bounded mask cases")

    started = time.time()
    source = Image.open(verified[0][1]).convert("RGB")
    load_started = time.time()
    lama = SimpleLama(device=torch.device("cuda"))
    load_seconds = time.time() - load_started
    outputs = []
    inference_seconds = 0.0
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict) or not isinstance(case.get("box"), list) or len(case["box"]) != 4:
            raise ValueError("invalid inpainting case")
        pad = max(0, min(int(case.get("pad", 6)), 64))
        box = _expanded_box(case["box"], source.width, source.height, pad)
        mask = Image.new("L", source.size, 0)
        ImageDraw.Draw(mask).rectangle(box, fill=255)
        infer_started = time.time()
        result = lama(source, mask)
        inference_seconds += time.time() - infer_started
        output_dir = MOUNT / "shards" / shard_id / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        name = f"inpaint_{index:04d}.png"
        path = output_dir / name
        result.save(path, format="PNG")
        outputs.append(
            {
                "remote_path": f"shards/{shard_id}/output/{name}",
                "filename": name,
                "sha256": _sha256_file(path),
                "case_name": str(case.get("name") or f"case_{index:04d}"),
                "box": list(box),
            }
        )
    return _write_result(
        shard,
        shard_id,
        outputs=outputs,
        timings={"model_load_seconds": load_seconds, "inference_seconds": inference_seconds, "remote_seconds": time.time() - started},
        function_name="inpainting_shard",
    )
