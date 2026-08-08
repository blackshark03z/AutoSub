from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _is_cjk(text: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in text
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--image")
    parser.add_argument("--manifest")
    args = parser.parse_args()
    if bool(args.image) == bool(args.manifest):
        parser.error("exactly one of --image or --manifest is required")
    config_path = Path(args.config).resolve()
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
        det_model_dir / "inference.pdmodel",
        det_model_dir / "inference.pdiparams",
        rec_model_dir / "inference.pdmodel",
        rec_model_dir / "inference.pdiparams",
        cls_model_dir / "inference.pdmodel",
        cls_model_dir / "inference.pdiparams",
    ]
    if not all(path.is_file() for path in required):
        print(json.dumps({"ok": False, "error": {"code": "ocr_model_missing"}}))
        return 2

    if args.image:
        images = [Path(args.image).resolve()]
    else:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
        images = [Path(value).resolve() for value in manifest.get("images", [])]
    if not images or not all(image.is_file() for image in images):
        print(json.dumps({"ok": False, "error": {"code": "ocr_image_missing"}}))
        return 2

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
    frames = []
    for index, image in enumerate(images):
        result = ocr.ocr(str(image), cls=True)
        items = []
        for page in result or []:
            for entry in page or []:
                if len(entry) < 2:
                    continue
                box, text_score = entry[0], entry[1]
                text, score = text_score if isinstance(text_score, (list, tuple)) and len(text_score) >= 2 else ("", 0.0)
                items.append(
                    {
                        "box": box,
                        "text": text,
                        "confidence": float(score or 0.0),
                        "contains_cjk": _is_cjk(str(text)),
                    }
                )
        frames.append({"index": index, "items": items})
    payload = {
        "ok": True,
        "frames": frames,
        "contains_cjk": any(item["contains_cjk"] for frame in frames for item in frame["items"]),
        "runtime": "paddleocr",
        "model_version": "paddleocr-2.10.0",
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
