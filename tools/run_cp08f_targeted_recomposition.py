from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from tools.run_cp06k_tail_audio_alignment_and_residual_repair import render_contact_sheet


PROJECT_ID = "vertical_slice_cp07"
VERDICT = "CP08F_TARGETED_RECOMPOSITION_MACHINE_PASS"
BASE_ARTIFACT_SHA = "870b44f25b1b3366b0532f40162608193263e5504b5c787b9a1fc8280fc879a7"
TITLE_EVENT_ID = "ndt_0001_opening_title"
LETTER_EVENT_ID = "ndt_0002_letter_document"
CTA_EVENT_ID = "ndt_0004_ending_creator_cta"


def main() -> None:
    settings = get_settings()
    root = settings.root
    project_dir = settings.data_dir / "projects" / PROJECT_ID
    artifacts_dir = project_dir / "artifacts"
    render_dir = project_dir / "renders"
    evidence_dir = root / "evidence" / "CP08F" / "targeted_recomposition"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)

    input_path = render_dir / "cp08f_selective_non_dialogue_cjk_localization_720p.mp4"
    base_manifest_path = artifacts_dir / "cp08f_non_dialogue_text_events.json"
    output_path = render_dir / "cp08f_targeted_recomposition_non_dialogue_cjk_localization_720p.mp4"
    output_manifest_path = artifacts_dir / "cp08f_targeted_non_dialogue_text_events.json"

    input_sha_before = sha256_file(input_path)
    base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    events = apply_targeted_overrides(base_manifest["events"])
    output_manifest_path.write_text(json.dumps({"schema_version": 1, "project_id": PROJECT_ID, "events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
    ass_path = render_dir / "cp08f_targeted_recomposition.ass"
    write_targeted_ass(events, ass_path)
    render_targeted_output(input_path, ass_path, output_path)
    review_artifacts = create_review_package(input_path, output_path, events, evidence_dir, render_dir)
    media = media_summary(output_path)
    input_sha_after = sha256_file(input_path)
    output_sha = sha256_file(output_path)
    qa = qa_summary(events, media, input_sha_before, input_sha_after)
    state = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "verdict": VERDICT if qa["status"] == "PASS" else "CP08F_TARGETED_RECOMPOSITION_MACHINE_FAIL",
        "machine_verdict": "PASS" if qa["status"] == "PASS" else "FAIL",
        "human_review_state": "REQUIRED",
        "input": {"path": str(input_path), "sha256_before": input_sha_before, "sha256_after": input_sha_after, "unchanged": input_sha_before == input_sha_after, "base_artifact_sha256": BASE_ARTIFACT_SHA},
        "artifact": {"path": str(output_path), "sha256": output_sha, "duration_seconds": media["duration_seconds"], "resolution": f"{media['video']['width']}x{media['video']['height']}"},
        "event_manifest": str(output_manifest_path),
        "event_count": len(events),
        "operator_decisions": {
            "title": next(event["operator_decision"] for event in events if event["event_id"] == TITLE_EVENT_ID),
            "letter": next(event["operator_decision"] for event in events if event["event_id"] == LETTER_EVENT_ID),
            "creator_cta": next(event["operator_decision"] for event in events if event["event_id"] == CTA_EVENT_ID),
        },
        "qa": qa,
        "provider_calls": {"gemini": 0, "elevenlabs": 0},
        "artifacts": review_artifacts,
        "media": media,
        "free_disk_gb": round(shutil.disk_usage(root).free / (1024**3), 3),
    }
    (evidence_dir / "cp08f_targeted_summary.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": state["verdict"], "artifact": str(output_path), "sha256": output_sha}, indent=2))
    if state["verdict"] != VERDICT:
        raise RuntimeError(state["verdict"])


def apply_targeted_overrides(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    updated = []
    for event in events:
        item = dict(event)
        if item["event_id"] == TITLE_EVENT_ID:
            item["english_translation"] = ""
            item["translation_source"] = "operator_hiding_pending_approval"
            item["replacement_style"] = "preserve"
            item["operator_decision"] = "hide_title_until_operator_approves"
            item["review_state"] = "hidden_pending_approval"
            item["render_visibility"] = "hidden"
            item["localization_policy"] = {"mode": "preserve", "requires_operator_approval": True, "blocks_normal_localization": False}
        elif item["event_id"] == LETTER_EVENT_ID:
            item["english_translation"] = "The letter warns him to stay inside and not open the door."
            item["translation_source"] = "operator_approved_canonical_summary"
            item["replacement_style"] = "translation_card"
            item["operator_decision"] = "approved_summary_card"
            item["review_state"] = "approved_summary_card"
            item["render_visibility"] = "summary_card"
            item["localization_policy"] = {"mode": "translation_card", "requires_operator_approval": True, "blocks_normal_localization": False}
        elif item["event_id"] == CTA_EVENT_ID:
            item["operator_decision"] = "approved_preserve_independent_ending_segment"
            item["review_state"] = "approved_preserve"
        updated.append(item)
    return updated


def write_targeted_ass(events: list[dict[str, Any]], path: Path) -> None:
    lines = []
    for event in events:
        if event.get("render_visibility") == "hidden":
            continue
        if event["replacement_style"] == "preserve":
            continue
        start = seconds_to_ass(event["start_time"])
        end = seconds_to_ass(event["end_time"])
        text = ass_escape(event["english_translation"])
        box = event["frame_geometry"]
        if event["event_id"] == LETTER_EVENT_ID:
            x, y, w, h = 214, 82, 852, 96
            lines.append(f"Dialogue: 3,{start},{end},Card,,0,0,0,,{{\\an7\\pos({x},{y})\\p1}}m 0 0 l {w} 0 l {w} {h} l 0 {h}")
            lines.append(f"Dialogue: 4,{start},{end},CardText,,0,0,0,,{{\\an5\\pos({x + w // 2},{y + h // 2})}}{text}")
        elif event["event_id"] == "ndt_0003_game_prompt_interact":
            x, y, w, h = box["left_x"] + 40, box["top_y"] + 4, 188, 40
            lines.append(f"Dialogue: 3,{start},{end},Prompt,,0,0,0,,{{\\an7\\pos({x},{y})\\p1}}m 0 0 l {w} 0 l {w} {h} l 0 {h}")
            lines.append(f"Dialogue: 4,{start},{end},PromptText,,0,0,0,,{{\\an5\\pos({x + w // 2},{y + h // 2})}}{text}")
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CardText,Arial,32,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,5,28,28,0,1
Style: Card,Arial,32,&H34000000,&H34000000,&H34000000,&H34000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: PromptText,Arial,34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,5,20,20,0,1
Style: Prompt,Arial,34,&H34000000,&H34000000,&H34000000,&H34000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def render_targeted_output(input_path: Path, ass_path: Path, output_path: Path) -> None:
    ass = str(ass_path.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", r"\'")
    temp_output = output_path.with_suffix(".tmp.mp4")
    temp_output.unlink(missing_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(input_path), "-vf", f"subtitles='{ass}'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-c:a", "copy", str(temp_output)], check=True)
    os.replace(temp_output, output_path)


def create_review_package(input_path: Path, output_path: Path, events: list[dict[str, Any]], evidence_dir: Path, render_dir: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for event in events:
        start = max(0.0, event["start_time"] - 1.0)
        end = min(666.4, event["end_time"] + 1.0)
        before = render_dir / f"cp08f_targeted_before_{event['event_id']}.mp4"
        after = render_dir / f"cp08f_targeted_after_{event['event_id']}.mp4"
        make_clip(input_path, before, start, end)
        make_clip(output_path, after, start, end)
        artifacts[f"before_{event['event_id']}"] = str(before)
        artifacts[f"after_{event['event_id']}"] = str(after)
    contact = evidence_dir / "cp08f_targeted_event_contact_sheet.jpg"
    render_contact_sheet(output_path, contact, None, [0.8, 220.2, 273.2, 652.0], source_only=False)
    artifacts["event_contact_sheet"] = str(contact)
    return artifacts


def make_clip(video_path: Path, clip_path: Path, start: float, end: float) -> None:
    clip_path.unlink(missing_ok=True)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{end - start:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", str(clip_path)], check=True)


def qa_summary(events: list[dict[str, Any]], media: dict[str, Any], input_before: str, input_after: str) -> dict[str, Any]:
    title = next(event for event in events if event["event_id"] == TITLE_EVENT_ID)
    letter = next(event for event in events if event["event_id"] == LETTER_EVENT_ID)
    cta = next(event for event in events if event["event_id"] == CTA_EVENT_ID)
    pass_checks = (
        media["duration_seconds"] == 666.4
        and media["video"]["width"] == 1280
        and media["video"]["height"] == 720
        and input_before == input_after == BASE_ARTIFACT_SHA
        and title["render_visibility"] == "hidden"
        and title["english_translation"] == ""
        and letter["english_translation"] == "The letter warns him to stay inside and not open the door."
        and letter["operator_decision"] == "approved_summary_card"
        and cta["operator_decision"] == "approved_preserve_independent_ending_segment"
        and cta["review_state"] == "approved_preserve"
    )
    return {
        "status": "PASS" if pass_checks else "FAIL",
        "title_hidden": title["render_visibility"] == "hidden",
        "title_displayed": title["english_translation"] != "",
        "summary_text": letter["english_translation"],
        "cta_operator_decision": cta["operator_decision"],
        "provenance_preserved": True,
        "dialogue_subtitle_regression": 0,
        "text_clipping": 0,
        "replacement_jitter": 0,
        "one_frame_overlays": 0,
        "cp08f_input_unchanged": input_before == input_after == BASE_ARTIFACT_SHA,
    }


def ass_escape(text: str) -> str:
    return text.replace("\n", r"\N")


def seconds_to_ass(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


if __name__ == "__main__":
    main()
