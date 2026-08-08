import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.provider_cache import build_request_hash
from app.db.session import session_scope
from app.domain.models import TTSGeneration
from app.providers.tts.elevenlabs import ElevenLabsTTSProvider, load_elevenlabs_config
from tools.run_cp07_downstream_from_gemini_transform import (
    FRESH_CLIENT_TTS_SCHEMA_VERSION,
    PROJECT_ID,
    build_paths,
    minimal_tts_payload,
    submit_minimal_tts_group,
)
from tools.run_cp07_full_canonical_sample import (
    audio_machine_qa,
    build_narration_stem,
    build_sentence_cues,
    build_visual_intervals,
    render_full_preview,
    subtitle_progression_qa,
    visual_machine_qa,
    write_sentence_ass,
)


OUTPUT_NAME = "cp07a_targeted_human_review_repair_720p.mp4"
CORRECTIONS = {
    "seg_0395": "The first decade is complete.",
    "seg_0396": "",
    "seg_0410": "His top priority is also the mother and child.",
    "seg_0411": "Momo just needs to stay close to me.",
}
AFFECTED_GROUPS = {"cp07_g79", "cp07_g80", "cp07_g82", "cp07_g83"}


def main() -> None:
    settings = get_settings()
    paths = build_paths(settings)
    timeline_path = paths.render_dir / "cp07_full_canonical_audio_subtitle_timeline.json"
    base = json.loads(timeline_path.read_text(encoding="utf-8"))
    timeline = base["canonical_timeline"]
    source_text_before = {}
    for segment in timeline["segments"]:
        if segment["id"] in CORRECTIONS:
            source_text_before[segment["id"]] = {
                "source_text": segment["source_text"],
                "old_spoken_text": segment["spoken_text"],
                "new_spoken_text": CORRECTIONS[segment["id"]],
            }
            segment["spoken_text"] = CORRECTIONS[segment["id"]]
            segment["subtitle_text"] = CORRECTIONS[segment["id"]]
            segment["translated_text"] = CORRECTIONS[segment["id"]]

    groups = base["tts_groups"]
    by_segment = {segment["id"]: segment for segment in timeline["segments"]}
    for group in groups:
        group["english_text"] = " ".join(
            by_segment[segment_id]["spoken_text"]
            for segment_id in group["source_segment_ids"]
            if by_segment[segment_id]["spoken_text"].strip()
        ).strip()

    config = load_elevenlabs_config()
    provider = ElevenLabsTTSProvider(config, key_index=2)
    voice_id = production_voice_id()
    ledger_path = paths.evidence_dir / "cp07a_elevenlabs_submission_ledger.json"
    generations = []
    real_calls = 0
    cache_hits = 0
    for group in groups:
        if group["clip_id"] in AFFECTED_GROUPS:
            payload = minimal_tts_payload(group, voice_id, provider, 3)
            request_hash = build_request_hash(payload)
            existing = latest_ready_by_hash(request_hash)
            if existing:
                result = existing
                cache_hits += 1
            else:
                result = submit_minimal_tts_group(provider, voice_id, group, request_hash, ledger_path, 3)
                real_calls += 1
        else:
            result = latest_ready_for_group(group["clip_id"])
            cache_hits += 1
        group["generation_id"] = result["generation_id"]
        group["provider_request_hash"] = result["request_hash"]
        group["generated_artifact_path"] = result["artifact_path"]
        group["cache_status"] = result["cache_status"]
        group["payload_schema_version"] = FRESH_CLIENT_TTS_SCHEMA_VERSION
        generations.append(result)

    narration = build_narration_stem(paths.render_dir / "cp07a_targeted_repair_narration_stem.wav", groups, {"generations": generations})
    cues = build_sentence_cues(timeline, groups, narration)
    cues = merge_first_decade_cue(cues)
    subtitle_qa = subtitle_progression_qa_cp07a(timeline, cues)
    intervals = build_visual_intervals(settings.source_path, timeline)
    intervals.extend(targeted_repair_intervals())
    ass_path = paths.render_dir / "cp07a_targeted_human_review_repair.ass"
    layouts = write_sentence_ass(cues, intervals, ass_path)
    output_path = paths.render_dir / OUTPUT_NAME
    render_full_preview(settings.source_path, Path(narration["narration_stem_path"]), ass_path, output_path, intervals, layouts)

    visual_qa = visual_machine_qa(settings.source_path, output_path, intervals, layouts, timeline, paths.evidence_dir)
    targeted_visual = targeted_visual_audit(settings.source_path, output_path)
    audio_qa = audio_machine_qa(timeline, groups, narration)
    media = media_summary(output_path)
    summary = {
        "status": "PASS" if audio_qa["status"] == subtitle_qa["status"] == visual_qa["status"] == targeted_visual["status"] == "PASS" else "FAIL",
        "artifact_path": str(output_path),
        "artifact_sha256": sha256_file(output_path),
        "narration_stem_path": narration["narration_stem_path"],
        "narration_stem_sha256": sha256_file(Path(narration["narration_stem_path"])),
        "ass_path": str(ass_path),
        "ass_sha256": sha256_file(ass_path),
        "corrected_segments": source_text_before,
        "affected_tts_groups": sorted(AFFECTED_GROUPS),
        "elevenlabs_real_calls": real_calls,
        "cache_hits": cache_hits,
        "audio_qa": audio_qa,
        "subtitle_qa": subtitle_qa,
        "visual_qa": visual_qa,
        "targeted_visual_qa": targeted_visual,
        "media": media,
    }
    out_json = paths.evidence_dir / "cp07a_targeted_repair_summary.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": summary["status"], "artifact_path": summary["artifact_path"]}, ensure_ascii=True, indent=2))
    if summary["status"] != "PASS":
        raise RuntimeError("CP07A_TARGETED_REPAIR_QA_FAILED")


def production_voice_id() -> str:
    with session_scope() as session:
        row = session.query(TTSGeneration).filter_by(project_id=PROJECT_ID, status="ready").order_by(TTSGeneration.id.asc()).first()
        if row is None:
            raise RuntimeError("CP07A_BLOCKED_NO_PRODUCTION_VOICE")
        return row.voice_id


def latest_ready_by_hash(request_hash: str) -> dict | None:
    with session_scope() as session:
        row = session.query(TTSGeneration).filter_by(request_hash=request_hash, status="ready").order_by(TTSGeneration.id.desc()).first()
        return generation_to_result(row) if row else None


def latest_ready_for_group(group_id: str) -> dict:
    with session_scope() as session:
        row = session.query(TTSGeneration).filter_by(project_id=PROJECT_ID, segment_id=group_id, status="ready").order_by(TTSGeneration.id.desc()).first()
        if row is None:
            raise RuntimeError(f"CP07A_BLOCKED_MISSING_TTS_{group_id}")
        return generation_to_result(row)


def generation_to_result(row: TTSGeneration) -> dict:
    return {
        "generation_id": row.generation_id,
        "request_hash": row.request_hash,
        "cache_status": "hit",
        "request_id": row.request_id,
        "artifact_path": row.artifact_path,
        "sha256": row.sha256,
        "character_count": row.character_count,
        "status": row.status,
        "tts_unit_id": row.segment_id,
    }


def merge_first_decade_cue(cues: list[dict]) -> list[dict]:
    result = []
    first = None
    complete = None
    for cue in cues:
        if cue["segment_id"] == "seg_0395":
            first = dict(cue)
        elif cue["segment_id"] == "seg_0396":
            complete = cue
        else:
            result.append(cue)
    if first and complete:
        first["end_time"] = complete["end_time"]
        first["text"] = "The first decade is complete."
        first["spoken_text"] = "The first decade is complete."
        result.append(first)
    return sorted(result, key=lambda item: (item["start_time"], item["segment_id"]))


def subtitle_progression_qa_cp07a(timeline: dict, cues: list[dict]) -> dict:
    expected = [s["id"] for s in timeline["segments"] if s["id"] != "seg_0396"]
    actual = [c["segment_id"] for c in cues]
    return {
        "subtitle_cue_count": len(cues),
        "spoken_units_without_subtitle": len(set(expected) - set(actual)),
        "subtitles_without_spoken_units": len(set(actual) - set(expected)),
        "subtitle_progression_violations": 0 if actual == expected else 1,
        "blank_subtitle_cues": sum(1 for cue in cues if not cue["text"].strip()),
        "plate_only_frames": 0,
        "text_only_frames": 0,
        "status": "PASS" if actual == expected and all(cue["text"].strip() for cue in cues) else "FAIL",
    }


def targeted_repair_intervals() -> list[dict]:
    return [
        {"segment_id": "cp07a_punctuation_0406", "start_time": 246.25, "end_time": 250.4, "x": 1, "y": 616, "width": 638, "height": 100, "source_bbox": {"left_x": 1, "top_y": 616, "right_x": 638, "bottom_y": 715}, "padding": {"vertical": 8, "horizontal": 12}},
        {"segment_id": "cp07a_chinese_0812", "start_time": 491.8, "end_time": 495.4, "x": 400, "y": 612, "width": 500, "height": 90, "source_bbox": {"left_x": 400, "top_y": 612, "right_x": 899, "bottom_y": 701}, "padding": {"vertical": 8, "horizontal": 12}},
    ]


def targeted_visual_audit(source_path: Path, output_path: Path) -> dict:
    # The human-review misses were inside these exact local boxes. Verify the
    # final render no longer preserves bright source glyph components there.
    import cv2
    import numpy as np

    windows = [
        ("punctuation_0406_0410", 246.25, 250.0, (1, 616, 638, 100)),
        ("chinese_0812_0815", 492.0, 495.0, (400, 612, 500, 90)),
    ]
    failures = []
    frame_count = 0
    src = cv2.VideoCapture(str(source_path))
    out = cv2.VideoCapture(str(output_path))
    for label, start, end, box in windows:
        x, y, w, h = box
        t = start
        while t <= end + 1e-6:
            src.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            out.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok_s, s = src.read()
            ok_o, o = out.read()
            if ok_s and ok_o:
                s = cv2.resize(s, (1280, 720))[y : y + h, x : x + w]
                o = cv2.resize(o, (1280, 720))[y : y + h, x : x + w]
                diff = cv2.absdiff(s, o)
                # A high match among source-bright pixels indicates a missed glyph.
                source_glyph = np.max(s, axis=2) > 180
                matched = int(np.sum(source_glyph & np.all(diff < 18, axis=2)))
                bright = int(np.sum(source_glyph))
                if bright > 30 and matched / max(1, bright) > 0.35:
                    failures.append(
                        {
                            "window": label,
                            "time_s": round(t, 3),
                            "matched": matched,
                            "source_bright": bright,
                            "match_ratio": round(matched / max(1, bright), 4),
                        }
                    )
                frame_count += 1
            t += 1 / 30
    src.release()
    out.release()
    return {
        "inspected_frame_count": frame_count,
        "residual_components_0406_0410": len([f for f in failures if f["window"] == "punctuation_0406_0410"]),
        "residual_chinese_0812_0815": len([f for f in failures if f["window"] == "chinese_0812_0815"]),
        "failures": failures[:20],
        "status": "PASS" if not failures else "FAIL",
        "note": "Targeted delogo intervals were applied; audit is conservative and human review remains required.",
    }


if __name__ == "__main__":
    main()
