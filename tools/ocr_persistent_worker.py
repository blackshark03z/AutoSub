from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from ocr_runtime_worker import _is_cjk


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _load_engine(config_path: Path):
    config_path = config_path.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    base = config_path.parent.parent
    def resolve(value: str) -> Path:
        path = Path(value)
        return (base / path if not path.is_absolute() else path).resolve()
    model_root = resolve(config["model_root"])
    det_model_dir = model_root / "ch_det"
    rec_model_dir = model_root / "ch_rec"
    cls_model_dir = model_root / "ch_cls"
    required = [
        det_model_dir / "inference.pdmodel", det_model_dir / "inference.pdiparams",
        rec_model_dir / "inference.pdmodel", rec_model_dir / "inference.pdiparams",
        cls_model_dir / "inference.pdmodel", cls_model_dir / "inference.pdiparams",
    ]
    if not all(path.is_file() for path in required):
        raise RuntimeError("ocr_model_missing")
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(
        use_angle_cls=True,
        lang="ch",
        use_gpu=False,
        det_model_dir=str(det_model_dir),
        rec_model_dir=str(rec_model_dir),
        cls_model_dir=str(cls_model_dir),
        show_log=False,
    )
    return ocr


def _run_manifest(ocr, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    images = [Path(value).resolve() for value in manifest.get("images", [])]
    if not images or not all(image.is_file() for image in images):
        raise RuntimeError("ocr_image_missing")
    frames=[]
    for index,image in enumerate(images):
        result=ocr.ocr(str(image),cls=True)
        items=[]
        for page in result or []:
            for entry in page or []:
                if len(entry)<2:
                    continue
                box,text_score=entry[0],entry[1]
                text,score=text_score if isinstance(text_score,(list,tuple)) and len(text_score)>=2 else ("",0.0)
                items.append({"box":box,"text":text,"confidence":float(score or 0.0),"contains_cjk":_is_cjk(str(text))})
        frames.append({"index":index,"items":items})
    return {
        "ok": True,
        "frames": frames,
        "contains_cjk": any(item["contains_cjk"] for frame in frames for item in frame["items"]),
        "runtime": "paddleocr",
        "model_version": "paddleocr-2.10.0",
    }


def main() -> None:
    ocr=None
    config_identity=None
    for raw in sys.stdin:
        raw=raw.strip()
        if not raw:
            continue
        try:
            command=json.loads(raw)
        except json.JSONDecodeError:
            continue
        if command.get("command")=="shutdown":
            break
        result_path=Path(str(command.get("result_path") or ""))
        try:
            config_path=Path(str(command["config_path"]))
            manifest_path=Path(str(command["manifest_path"]))
            identity=str(config_path.resolve())
            if ocr is None or identity!=config_identity:
                ocr=_load_engine(config_path)
                config_identity=identity
            _write_json(result_path,_run_manifest(ocr,manifest_path))
        except Exception as exc:
            _write_json(result_path,{"ok":False,"error":{"code":"ocr_worker_failed","detail":type(exc).__name__}})


if __name__ == "__main__":
    main()
