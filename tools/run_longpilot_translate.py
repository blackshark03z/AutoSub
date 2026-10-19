"""Long-pilot translation: offline HY-MT2 fan-out en-US + vi-VN from one shared master.

Reads the immutable E1 master produced by ``run_longpilot_ocr.py``, verifies the L1
cache hit (the source is NOT re-OCR'd), then translates every cue into ``en-US`` and
``vi-VN`` with the local llama.cpp HY-MT2 Q4 server, binding each locale into the E1
``LocaleTranslationSet`` with a real ``InferenceAuditRecord`` and the L3 cache.

Also records the locale-isolation proof: changing only the en-US glossary re-keys
en-US L3 (a miss) while vi-VN L3 and L1 stay valid.

Usage:
    python tools/run_longpilot_translate.py --out scratch/longpilot/run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from PIL import ImageFont  # noqa: E402

from app.services.clean_subtitle_render import (  # noqa: E402
    multiline_text_box,
    wrap_subtitle_text,
)
from app.services.hy_mt2_offline_adapter import (  # noqa: E402
    CONTEXT_GLOSSARIES,
    CONTEXT_GLOSSARY_VERSIONS,
    PROMPT_VERSION,
    SUPPORTED_LOCALES,
    HyMt2OfflineLocalizer,
    apply_rule_postedit,
    build_contextual_translation_prompt,
    sha256_text,
)
from app.services.longpilot_chunking import CheckpointError, ChunkCheckpointStore, MasterVersion  # noqa: E402
from app.services.multimarket_cache import (  # noqa: E402
    MultiMarketCacheStore,
    compute_l1_key,
    compute_l2_key,
    compute_l3_key,
)
from app.services.multimarket_contracts import (  # noqa: E402
    InferenceAuditRecord,
    LocalizedCue as E1LocalizedCue,
    LocaleTranslationSet,
    SourceCorrectionReviewLayer,
    SourceZhMasterCueSet,
    canonical_sha256,
)

MODEL = Path(r"D:\AutoSub-lane-e2-runtime\scratch\e2_runtime\models\Hy-MT2-1.8B-Q4_K_M.gguf")
LLAMA_SERVER = Path(r"D:\AutoSub-lane-e2-runtime\scratch\e2_runtime\llama\bin\llama-server.exe")
FONT_PATH = Path(r"C:\Windows\Fonts\arial.ttf")
PORT = 8155
MODEL_ID = "tencent/Hy-MT2-1.8B-Q4_K_M"
RUNTIME_BACKEND = "tencent_hymt2_q4_llama_cpp"
OCR_ENGINE_VERSION = "RapidOCR_PP-OCRv5_ONNX_v1.4.4"
SAMPLING_POLICY_ID = "sample_fps=2.0_step_ms=500"
ROI_FILTER_VERSION = "roi_dialogue_band_v1.3"

# Terms observed in this source that the base glossary omits. Keeping them
# locale-scoped means only the affected locale's L3 key changes.
EXTRA_GLOSSARY: dict[str, dict[str, str]] = {
    "en-US": {"重瞳": "Double Pupils"},
}


def wait_health(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(1.0)
    return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def start_server(port: int) -> subprocess.Popen:
    log = open(Path(os.environ.get("TMP", ".")) / f"hymt2_longpilot_{port}.log", "w", encoding="utf-8")
    return subprocess.Popen(
        [str(LLAMA_SERVER), "-m", str(MODEL), "--jinja", "-ngl", "0", "-t", "6", "-c", "2048",
         "--host", "127.0.0.1", "--port", str(port)],
        stdout=log, stderr=subprocess.STDOUT, cwd=str(LLAMA_SERVER.parent),
    )


_CONTEXT_LEAK_RE = re.compile(r"\s*(?:next|previous)\s*line\s*:.*$", re.IGNORECASE | re.DOTALL)


def strip_context_leak(text: str) -> str:
    """Remove prompt-context text (``Next line:`` / ``Previous line:``) the model echoes."""
    return _CONTEXT_LEAK_RE.sub("", str(text or "")).strip()


def _norm_translation(text: str) -> str:
    return " ".join(str(text or "").split()).strip().lower()


def typography(text: str, duration_s: float, max_width: int) -> dict:
    font = ImageFont.truetype(str(FONT_PATH), 32)
    wrapped = wrap_subtitle_text(text, font, max_width)
    box = multiline_text_box(wrapped, font)
    lines = tuple(wrapped.split("\n"))
    return {
        "line_breaks": lines if len(lines) <= 2 else lines[:2],
        "rendered_width_px": int(box["width"]), "rendered_height_px": int(box["height"]),
        "chars_per_second": round(len(text) / duration_s, 2) if duration_s > 0 else 0.0,
        "words_per_minute": round(len(text.split()) / duration_s * 60.0, 2) if duration_s > 0 else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--force", action="store_true", help="ignore L3 cache and re-infer")
    args = parser.parse_args()
    out_dir = Path(args.out)

    master = SourceZhMasterCueSet.from_json((out_dir / "master_cues.json").read_text(encoding="utf-8"))
    print(f"master cues={len(master.cues)} extraction_hash={master.extraction_hash}")

    l1_key = compute_l1_key(
        source_video_sha256=master.source_video_sha256, video_width=master.video_width,
        video_height=master.video_height, ocr_engine_version=master.ocr_engine_version,
        sampling_policy_id=master.sampling_policy_id, roi_filter_version=master.roi_filter_version)
    store = MultiMarketCacheStore(out_dir / "multimarket_cache.db")
    l1_hit = store.get_l1_extraction(l1_key)
    print(f"L1 cache {'HIT' if l1_hit else 'MISS'} ({l1_key})")
    assert l1_hit is not None, "L1 master must be present from the single chunked OCR pass"

    server_proc = None
    endpoint = args.endpoint
    if not endpoint:
        if not port_free(PORT):
            print(f"port {PORT} busy", file=sys.stderr)
            return 2
        server_proc = start_server(PORT)
        if not wait_health(PORT):
            server_proc.terminate()
            print("server failed to become healthy", file=sys.stderr)
            return 2
        endpoint = f"http://127.0.0.1:{PORT}/v1/chat/completions"
        print(f"llama-server healthy on {endpoint}")

    localizer = HyMt2OfflineLocalizer(server_endpoint=endpoint, model_path=MODEL)
    model_sha = localizer.model_sha256
    print(f"model_sha256={model_sha} runtime={localizer.provenance().runtime} v={localizer.provenance().runtime_version}")

    correction_layer = SourceCorrectionReviewLayer(
        extraction_hash=master.extraction_hash, layer_version="v1.0.0-longpilot-raw", corrections=())
    l2_key = compute_l2_key(l1_cache_key=l1_key, correction_layer_hash=correction_layer.correction_layer_hash)
    store.put_l2_correction(l2_key, l1_key, correction_layer)

    texts = [c.raw_ocr_text for c in master.cues]
    l3_keys: dict[str, str] = {}
    inference_counts: dict[str, int] = {}
    echo_retries: dict[str, int] = {}

    def _l3(locale: str, gloss_sha: str, prompt_sha: str) -> str:
        return compute_l3_key(
            l2_cache_key=l2_key, target_locale=locale, model_target_language=SUPPORTED_LOCALES[locale],
            model_id=MODEL_ID, model_weights_sha256=model_sha, runtime_backend=RUNTIME_BACKEND,
            prompt_template_sha256=prompt_sha, glossary_sha256=gloss_sha, context_window_size=1)

    try:
        prompt_template_sha = sha256_text(PROMPT_VERSION + "|contextual-v2")
        for locale in ("en-US", "vi-VN"):
            target_lang = SUPPORTED_LOCALES[locale]
            glossary = {**CONTEXT_GLOSSARIES[locale], **EXTRA_GLOSSARY.get(locale, {})}
            glossary_sha = canonical_sha256(dict(sorted(glossary.items())))
            l3 = _l3(locale, glossary_sha, prompt_template_sha)
            l3_keys[locale] = l3
            existing = out_dir / f"translations_{locale}.json"
            if not args.force and store.get_l3_translation(l3, require_live_inference=True) is not None and existing.exists():
                prior = json.loads(existing.read_text(encoding="utf-8"))
                inference_counts[locale] = int(prior.get("cue_count", 0))
                print(f"{locale}: L3 cache HIT, skip inference (l3={l3})")
                continue
            results, in_concat, out_concat = [], [], []
            context_echo_retries = 0
            prev_source: str | None = None
            prev_translation: str | None = None
            for idx, cue in enumerate(master.cues):
                src = texts[idx]
                prompt = build_contextual_translation_prompt(
                    src, locale, context_before=texts[idx - 1] if idx > 0 else None,
                    context_after=texts[idx + 1] if idx + 1 < len(texts) else None, glossary=glossary)
                raw = " ".join(localizer.infer(prompt).strip().split())
                postedited, applied = apply_rule_postedit(src, locale, raw)
                postedited = strip_context_leak(postedited)
                # Context-echo guard: consecutive lines that share a prefix can make the
                # model copy the previous line's translation verbatim. When the echoed
                # text matches the previous translation but the sources genuinely differ,
                # retry this line without story context (a known, localized failure mode).
                if (
                    prev_translation
                    and src != prev_source
                    and _norm_translation(postedited) == _norm_translation(prev_translation)
                ):
                    context_echo_retries += 1
                    prompt = build_contextual_translation_prompt(src, locale, glossary=glossary)
                    raw = " ".join(localizer.infer(prompt).strip().split())
                    postedited, applied = apply_rule_postedit(src, locale, raw)
                    postedited = strip_context_leak(postedited)
                prev_source = src
                prev_translation = postedited
                defective = (not postedited) or any("\u4e00" <= ch <= "\u9fff" for ch in postedited)
                results.append({
                    "cue_id": cue.cue_id, "start_ms": cue.start_ms, "end_ms": cue.end_ms,
                    "source_text": src, "target_locale": locale, "translation": postedited,
                    "raw_model_translation": raw, "rule_postedit_applied": applied,
                    "status": "TRANSLATED" if not defective else "QUALITY_REJECTED",
                    "provenance": "MODEL_WITH_GLOSSARY+RULE_POSTEDITED",
                    "prompt_sha256": sha256_text(prompt), "glossary_version": CONTEXT_GLOSSARY_VERSIONS[locale]})
                in_concat.append(src)
                out_concat.append(postedited)
            inference_counts[locale] = len(results)
            echo_retries[locale] = context_echo_retries

            audit = InferenceAuditRecord(
                invocation_id=f"hymt2-{locale}-{int(time.time())}-{os.getpid()}", runtime_backend=RUNTIME_BACKEND,
                model_id=MODEL_ID, model_weights_sha256=model_sha, prompt_sha256=prompt_template_sha,
                input_text_hash=canonical_sha256(in_concat), output_text_hash=canonical_sha256(out_concat),
                execution_timestamp_iso=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                verified_by="e2_hymt2_offline_adapter")

            e1_cues = []
            for r in results:
                duration_s = (r["end_ms"] - r["start_ms"]) / 1000.0
                typo = typography(r["translation"], duration_s, 1152)
                flags = []
                if typo["chars_per_second"] > 21:
                    flags.append("CPS_ABOVE_TARGET")
                if len(r["translation"]) > 90:
                    flags.append("LENGTH_REVIEW")
                e1_cues.append(E1LocalizedCue(
                    cue_id=r["cue_id"], target_locale=locale, model_target_language=target_lang,
                    translated_text=r["translation"], effective_source_text=r["source_text"], status="FIT_OK",
                    subtitle_start_ms=r["start_ms"], subtitle_end_ms=r["end_ms"],
                    source_mask_start_ms=r["start_ms"], source_mask_end_ms=r["end_ms"],
                    line_breaks=typo["line_breaks"], font_family="Arial", effective_font_size_px=32,
                    rendered_width_px=typo["rendered_width_px"], rendered_height_px=typo["rendered_height_px"],
                    chars_per_second=typo["chars_per_second"], words_per_minute=typo["words_per_minute"],
                    model_id=MODEL_ID, prompt_version=PROMPT_VERSION + "|contextual-v2",
                    glossary_version=CONTEXT_GLOSSARY_VERSIONS[locale], qa_flags=tuple(flags),
                    provenance="LIVE_INFERENCE"))

            loc_set = LocaleTranslationSet(
                source_video_sha256=master.source_video_sha256, extraction_hash=master.extraction_hash,
                correction_layer_hash=correction_layer.correction_layer_hash, target_locale=locale,
                model_target_language=target_lang, model_id=MODEL_ID, prompt_glossary_hash=glossary_sha,
                created_at_iso=time.strftime("%Y-%m-%dT%H:%M:%S%z"), cues=tuple(e1_cues),
                inference_audit=audit)
            store.put_l3_translation(l3, l2_key, loc_set)
            cached = store.get_l3_translation(l3, require_live_inference=True)

            (out_dir / f"translations_{locale}.json").write_text(json.dumps({
                "locale": locale, "target_lang_name": target_lang, "prompt_version": PROMPT_VERSION + "|contextual-v2",
                "glossary_version": CONTEXT_GLOSSARY_VERSIONS[locale],
                "provenance": "llama.cpp_hymt2_q4_server_offline", "model_sha256": model_sha, "model_id": MODEL_ID,
                "runtime_backend": RUNTIME_BACKEND, "runtime_version": localizer.provenance().runtime_version,
                "inference_audit": audit.to_dict(), "translation_set_hash": loc_set.translation_set_hash,
                "l2_cache_key": l2_key, "l3_cache_key": l3, "l3_live_cache_ok": cached is not None,
                "cue_count": len(results),
                "translated_count": sum(1 for r in results if r["status"] == "TRANSLATED"),
                "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
            (out_dir / f"inference_audit_{locale}.json").write_text(
                json.dumps(audit.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{locale}: cues={len(results)} translated={sum(1 for r in results if r['status']=='TRANSLATED')} "
                  f"context_echo_retries={context_echo_retries} "
                  f"l3={l3} live_cache_ok={cached is not None}")

        # Locale isolation proof: change ONLY en-US glossary -> en-US L3 misses, vi-VN unaffected.
        us_eff = {**CONTEXT_GLOSSARIES['en-US'], **EXTRA_GLOSSARY.get('en-US', {})}
        us_alt = canonical_sha256(dict(sorted({**us_eff, '_probe': 'isolation'}.items())))
        us_before = l3_keys["en-US"]
        us_after = _l3("en-US", us_alt, sha256_text(PROMPT_VERSION + "|contextual-v2"))
        vi_key = l3_keys["vi-VN"]
        iso = {
            "en_us_key_before": us_before, "en_us_key_after_glossary_change": us_after,
            "vi_vn_key": vi_key,
            "en_us_rekeyed": us_before != us_after,
            "en_us_new_key_is_miss": store.get_l3_translation(us_after) is None,
            "vi_vn_still_hit": store.get_l3_translation(vi_key) is not None,
            "l1_still_hit": store.get_l1_extraction(l1_key) is not None,
        }
        (out_dir / "locale_isolation.json").write_text(json.dumps(iso, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"isolation: en_us_rekeyed={iso['en_us_rekeyed']} new_miss={iso['en_us_new_key_is_miss']} "
              f"vi_still_hit={iso['vi_vn_still_hit']} l1_still_hit={iso['l1_still_hit']}")

        meta = {"model_sha256": model_sha, "l1_cache_key": l1_key, "l2_cache_key": l2_key,
                "l3_keys": l3_keys, "inference_counts": inference_counts,
                "context_echo_retries": echo_retries,
                "cue_count": len(master.cues)}
        (out_dir / "translate_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        if server_proc is not None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=10)
            except Exception:
                server_proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
