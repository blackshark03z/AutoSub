from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    packages = Path(args.packages).resolve()
    model_dir = packages / args.model_id
    if not (model_dir / "model" / "model.bin").is_file():
        print(json.dumps({"ok": False, "error": {"code": "translation_model_missing"}}))
        return 2
    request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
    texts = request.get("texts") if isinstance(request, dict) else None
    if not isinstance(texts, list) or not texts or any(not str(text).strip() for text in texts):
        print(json.dumps({"ok": False, "error": {"code": "translation_input_invalid"}}))
        return 2
    os.environ["ARGOS_PACKAGES_DIR"] = str(packages)
    import argostranslate.translate

    translations = []
    for text in texts:
        translation = argostranslate.translate.get_translation_from_codes("zh", "en")
        hypotheses = translation.hypotheses(str(text), num_hypotheses=4)
        selected = hypotheses[0]
        if len(str(text).strip()) <= 4 and str(text).strip().endswith("来了"):
            direct_arrival = next(
                (item for item in hypotheses if re.search(r"\b(here|arrived|home)\b", item.value, re.IGNORECASE)),
                None,
            )
            if direct_arrival is not None:
                selected = direct_arrival
        value = selected.value.strip()
        if not value:
            print(json.dumps({"ok": False, "error": {"code": "translation_empty"}}))
            return 2
        translations.append({"text": value, "confidence": float(selected.score)})
    print(json.dumps({
        "ok": True,
        "translations": translations,
        "runtime": "argostranslate",
        "model": args.model_id,
        "external_calls": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
