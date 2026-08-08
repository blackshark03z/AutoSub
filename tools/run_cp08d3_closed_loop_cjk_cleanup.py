import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.services.cjk_cleanup import build_cjk_cleanup_summary, save_cjk_cleanup_state
from app.services.ocr_runtime import is_cjk_text, run_ocr_on_image
from tools.run_cp06k_tail_audio_alignment_and_residual_repair import (
    FPS,
    HEIGHT,
    WIDTH,
)
from tools.run_cp06k_tail_audio_alignment_and_residual_repair import (
    detect_source_subtitle_bbox,
    glyph_pixel_count,
    matched_glyph_pixel_count,
    read_frame,
    render_contact_sheet,
    render_truth_audit,
    union_boxes,
)
from tools.run_cp07_full_canonical_sample import audio_machine_qa, render_full_preview, subtitle_progression_qa, visual_machine_qa


PROJECT_ID = "vertical_slice_cp07"
SOURCE_WINDOWS = [
    {
        "issue_id": "cjk_0406_punctuation_residual",
        "label": "Residual punctuation around 04:06-04:10",
        "start_time": 244.0,
        "end_time": 252.0,
        "expected_text_hint": "？？？",
        "category": "dialogue_subtitle",
    },
    {
        "issue_id": "cjk_0812_chinese_residual",
        "label": "Residual Chinese subtitle around 08:12-08:15",
        "start_time": 490.0,
        "end_time": 497.0,
        "expected_text_hint": "太阳已经落下",
        "category": "dialogue_subtitle",
    },
]


@dataclass(frozen=True)
class WindowScan:
    issue_id: str
    label: str
    category: str
    expected_text_hint: str
    start_time: float
    end_time: float
    detected_frames: list[dict]
    source_bbox: dict | None
    ocr_samples: list[dict]
    line_count: int
    text_class: str
    text_sample: str
    segment_id: str | None


def main() -> None:
    settings = get_settings()
    root = settings.root
    project_dir = settings.data_dir / "projects" / PROJECT_ID
    render_dir = project_dir / "renders"
    evidence_dir = root / "evidence" / "CP08D3" / "closed_loop_cjk_cleanup"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    before_contact = evidence_dir / "before_contact_sheet.jpg"
    after_contact = evidence_dir / "after_contact_sheet.jpg"
    paired_dir = evidence_dir / "paired_frames"
    paired_dir.mkdir(parents=True, exist_ok=True)

    source_path = settings.source_path
    cp07a_path = render_dir / "cp07a_targeted_human_review_repair_720p.mp4"
    cp07_full_timeline_path = render_dir / "cp07_full_canonical_audio_subtitle_timeline.json"
    narration_path = render_dir / "cp07_full_canonical_narration_stem.wav"
    ass_path = render_dir / "cp07_full_canonical_sentence_level.ass"
    output_path = render_dir / "cp08d_closed_loop_cjk_cleanup_720p.mp4"

    cp07a_sha_before = sha256_file(cp07a_path)
    timeline_payload = json.loads(cp07_full_timeline_path.read_text(encoding="utf-8"))
    source_summary = timeline_payload["source"]
    canonical = timeline_payload["canonical_timeline"]
    base_intervals = list(timeline_payload["visual_intervals"])
    subtitle_layouts = list(timeline_payload["subtitle_layouts"])
    base_qa = timeline_payload["qa"]

    scans = [scan_window(source_path, window, evidence_dir, canonical) for window in SOURCE_WINDOWS]
    cleanup_intervals = [build_cleanup_interval(scan) for scan in scans]
    target_intervals = base_intervals + cleanup_intervals
    target_intervals = sorted(target_intervals, key=lambda item: (item["start_time"], item["x"]))

    render_if_missing(source_path, narration_path, ass_path, output_path, target_intervals, subtitle_layouts)
    initial_scan = scan_repaired_output(source_path, output_path, scans, evidence_dir)
    repair_iteration = 1
    applied_intervals = cleanup_intervals
    final_scan = initial_scan

    if not final_scan["status"] == "PASS":
        repair_iteration = 2
        widened_intervals = [widen_interval(interval, extra_h=8, extra_v=4) for interval in cleanup_intervals]
        target_intervals = sorted(base_intervals + widened_intervals, key=lambda item: (item["start_time"], item["x"]))
        render_full_preview(source_path, narration_path, ass_path, output_path, target_intervals, subtitle_layouts)
        final_scan = scan_repaired_output(source_path, output_path, scans, evidence_dir, suffix="retry")
        applied_intervals = widened_intervals

    before_times = sorted({round(item.start_time, 2) for item in scans} | {round(item.end_time, 2) for item in scans} | {5.0, 22.0, 38.0, 50.0, 65.0})
    after_times = before_times
    render_contact_sheet(source_path, before_contact, None, before_times, source_only=False)
    render_contact_sheet(output_path, after_contact, None, after_times, source_only=False)
    make_paired_frames(source_path, output_path, scans, paired_dir)

    visual_truth = render_truth_audit(source_path, output_path, target_intervals, canonical, evidence_dir)
    visual_qa = visual_machine_qa(source_path, output_path, target_intervals, subtitle_layouts, canonical, evidence_dir)
    audio_qa = audio_machine_qa(canonical, timeline_payload["tts_groups"], timeline_payload["narration"])
    subtitle_qa = subtitle_progression_qa(canonical, timeline_payload["tts_groups"], timeline_payload["sentence_cues"])
    media = media_summary(output_path)
    cp07a_sha_after = sha256_file(cp07a_path)

    cleanup_state = build_cleanup_state(
        scans=scans,
        intervals=applied_intervals,
        source_path=source_path,
        output_path=output_path,
        source_summary=source_summary,
        cp07a_sha=cp07a_sha_before,
        render_sha=sha256_file(output_path),
        before_contact=before_contact,
        after_contact=after_contact,
        paired_dir=paired_dir,
        visual_truth=visual_truth,
        visual_qa=visual_qa,
        audio_qa=audio_qa,
        subtitle_qa=subtitle_qa,
        media=media,
        base_qa=base_qa,
        repair_iteration=repair_iteration,
        cp07a_sha_after=cp07a_sha_after,
        cp07_full_timeline_path=cp07_full_timeline_path,
        narration_path=narration_path,
        ass_path=ass_path,
        final_scan=final_scan,
    )
    state_path = save_cjk_cleanup_state(cleanup_state, root)
    summary_path = evidence_dir / "cleanup_summary.json"
    summary_path.write_text(json.dumps(cleanup_state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "verdict": cleanup_state["verdict"],
            "artifact_path": cleanup_state["artifact"]["path"],
            "artifact_sha256": cleanup_state["artifact"]["sha256"],
            "state_path": str(state_path),
            "detected_candidates": cleanup_state["detected_candidates"],
            "repair_iteration": cleanup_state["repair_iteration"],
        },
        ensure_ascii=False,
        indent=2,
    ))
    if cleanup_state["verdict"] != "CP08D_CLOSED_LOOP_RESIDUAL_CJK_CLEANUP_MACHINE_PASS":
        raise RuntimeError("CP08D_CLOSED_LOOP_RESIDUAL_CJK_CLEANUP_MACHINE_FAIL")


def scan_window(source_path: Path, window: dict, evidence_dir: Path, canonical: dict) -> WindowScan:
    cap = cv2.VideoCapture(str(source_path))
    hits: list[dict] = []
    ocr_samples: list[dict] = []
    mid = round((window["start_time"] + window["end_time"]) / 2, 3)
    sample_times = sorted(
        {
            round(window["start_time"], 3),
            round(window["start_time"] + 0.10, 3),
            round(window["start_time"] + 0.20, 3),
            round(window["start_time"] + 0.30, 3),
            round(mid - 0.20, 3),
            round(mid - 0.10, 3),
            round(mid, 3),
            round(mid + 0.10, 3),
            round(mid + 0.20, 3),
            round(window["end_time"] - 0.30, 3),
            round(window["end_time"] - 0.20, 3),
            round(window["end_time"] - 0.10, 3),
            round(window["end_time"], 3),
        }
    )
    for t in sample_times:
        frame = read_frame(cap, t)
        bbox, meta = detect_source_subtitle_bbox(frame)
        if bbox:
            hits.append({"time_s": round(t, 3), "bbox": bbox, "meta": meta})
    cap.release()
    if not hits:
        raise RuntimeError(f"BLOCKED_NO_SOURCE_CANDIDATE_{window['issue_id']}")
    source_bbox = union_boxes([item["bbox"] for item in hits])
    assert source_bbox is not None
    sample_times = [hits[0]["time_s"], hits[len(hits) // 2]["time_s"], hits[-1]["time_s"]]
    for index, time_s in enumerate(sample_times):
        frame = frame_at(source_path, time_s)
        crop_path = evidence_dir / f"{window['issue_id']}_ocr_{index + 1}.png"
        crop = crop_frame(frame, source_bbox, pad_x=24, pad_y=16)
        cv2.imwrite(str(crop_path), crop)
        ocr = run_ocr_on_image(crop_path)
        ocr_samples.append({"time_s": time_s, "path": str(crop_path), "ocr": ocr, "text": " | ".join(item.get("text", "") for item in ocr.get("items", []))})
    segment_id = segment_for_time(canonical, sample_times[1])
    text_sample = " ".join(sample["text"] for sample in ocr_samples).strip()
    line_count = max(1, max(len(item.get("ocr", {}).get("items", [])) for item in ocr_samples))
    text_class = "punctuation_residual" if "？" in text_sample or "???" in text_sample else ("dialogue_subtitle" if any(sample["ocr"].get("contains_cjk") for sample in ocr_samples) else "unknown")
    return WindowScan(
        issue_id=window["issue_id"],
        label=window["label"],
        category=window["category"],
        expected_text_hint=window["expected_text_hint"],
        start_time=window["start_time"],
        end_time=window["end_time"],
        detected_frames=hits,
        source_bbox=source_bbox,
        ocr_samples=ocr_samples,
        line_count=line_count,
        text_class=text_class,
        text_sample=text_sample,
        segment_id=segment_id,
    )


def render_if_missing(source_path: Path, narration_path: Path, ass_path: Path, output_path: Path, intervals: list[dict], layouts: list[dict]) -> None:
    if output_path.exists():
        try:
            media = media_summary(output_path)
            if (
                abs(float(media["duration_seconds"]) - 666.4) < 0.1
                and media["video"]["width"] == WIDTH
                and media["video"]["height"] == HEIGHT
            ):
                return
        except Exception:
            pass
    render_full_preview(source_path, narration_path, ass_path, output_path, intervals, layouts)


def build_cleanup_interval(scan: WindowScan) -> dict:
    bbox = scan.source_bbox or {"left_x": 0, "top_y": 0, "right_x": WIDTH - 1, "bottom_y": HEIGHT - 1}
    x = max(0, bbox["left_x"] - 12)
    y = max(0, bbox["top_y"] - 8)
    right = min(WIDTH - 1, bbox["right_x"] + 12)
    bottom = min(HEIGHT - 1, bbox["bottom_y"] + 8)
    return {
        "segment_id": scan.issue_id,
        "start_time": max(0.0, round(scan.detected_frames[0]["time_s"] - 4 / FPS, 3)),
        "end_time": round(scan.detected_frames[-1]["time_s"] + 5 / FPS, 3),
        "x": x,
        "y": y,
        "width": right - x + 1,
        "height": bottom - y + 1,
        "line_count": scan.line_count,
        "padding": {"vertical": 8, "horizontal": 12},
        "source_bbox": bbox,
        "source_only": True,
        "temporal_role": "cjk_cleanup",
        "reason": scan.label,
    }


def widen_interval(interval: dict, extra_h: int, extra_v: int) -> dict:
    bbox = interval["source_bbox"]
    x = max(0, bbox["left_x"] - 12 - extra_h)
    y = max(0, bbox["top_y"] - 8 - extra_v)
    right = min(WIDTH - 1, bbox["right_x"] + 12 + extra_h)
    bottom = min(HEIGHT - 1, bbox["bottom_y"] + 8 + extra_v)
    updated = dict(interval)
    updated["x"] = x
    updated["y"] = y
    updated["width"] = right - x + 1
    updated["height"] = bottom - y + 1
    updated["padding"] = {"vertical": 8 + extra_v, "horizontal": 12 + extra_h}
    return updated


def scan_repaired_output(source_path: Path, output_path: Path, scans: list[WindowScan], evidence_dir: Path, suffix: str = "initial") -> dict:
    source_cap = cv2.VideoCapture(str(source_path))
    output_cap = cv2.VideoCapture(str(output_path))
    residuals = []
    by_window = []
    for scan in scans:
        window_residuals = []
        for hit in scan.detected_frames:
            time_s = hit["time_s"]
            source_frame = read_frame(source_cap, time_s)
            output_frame = read_frame(output_cap, time_s)
            bbox = hit["bbox"]
            source_count = glyph_pixel_count(source_frame, bbox)
            output_count = glyph_pixel_count(output_frame, bbox)
            matched_count = matched_glyph_pixel_count(source_frame, output_frame, bbox)
            residual_ratio = round(matched_count / max(1, source_count), 4) if source_count else 0.0
            crop = crop_frame(output_frame, bbox, pad_x=24, pad_y=16)
            crop_path = evidence_dir / f"{scan.issue_id}_{suffix}_{time_s:.3f}.png"
            cv2.imwrite(str(crop_path), crop)
            ocr = run_ocr_on_image(crop_path)
            ocr_text = " ".join(item.get("text", "") for item in ocr.get("items", []))
            contains_cjk = bool(ocr.get("contains_cjk")) or is_cjk_text(ocr_text)
            entry = {
                "issue_id": scan.issue_id,
                "time_s": time_s,
                "source_count": source_count,
                "output_count": output_count,
                "matched_count": matched_count,
                "residual_ratio": residual_ratio,
                "contains_cjk": contains_cjk,
                "ocr_text": ocr_text,
                "bbox": bbox,
                "crop_path": str(crop_path),
            }
            if contains_cjk or ("？" in ocr_text or "???" in ocr_text):
                residuals.append(entry)
                window_residuals.append(entry)
        by_window.append({"issue_id": scan.issue_id, "residual_frames": window_residuals})
    source_cap.release()
    output_cap.release()
    return {
        "status": "PASS" if not residuals else "FAIL",
        "residual_frame_count": len(residuals),
        "residuals": residuals[:40],
        "by_window": by_window,
    }


def frame_at(video_path: Path, time_s: float) -> cv2.Mat:
    cap = cv2.VideoCapture(str(video_path))
    frame = read_frame(cap, time_s)
    cap.release()
    return frame


def crop_frame(frame, bbox: dict, pad_x: int, pad_y: int):
    x0 = max(0, bbox["left_x"] - pad_x)
    y0 = max(0, bbox["top_y"] - pad_y)
    x1 = min(WIDTH - 1, bbox["right_x"] + pad_x)
    y1 = min(HEIGHT - 1, bbox["bottom_y"] + pad_y)
    return frame[y0 : y1 + 1, x0 : x1 + 1]


def segment_for_time(canonical: dict, time_s: float) -> str | None:
    ms = int(round(time_s * 1000))
    for segment in canonical["segments"]:
        if segment["start_ms"] <= ms <= segment["end_ms"]:
            return segment["id"]
    return None


def make_paired_frames(source_path: Path, output_path: Path, scans: list[WindowScan], paired_dir: Path) -> None:
    source_cap = cv2.VideoCapture(str(source_path))
    output_cap = cv2.VideoCapture(str(output_path))
    for scan in scans:
        for index, hit in enumerate([scan.detected_frames[0], scan.detected_frames[len(scan.detected_frames)//2], scan.detected_frames[-1]]):
            time_s = hit["time_s"]
            source_frame = read_frame(source_cap, time_s)
            output_frame = read_frame(output_cap, time_s)
            pair = cv2.hconcat([cv2.resize(source_frame, (640, 360)), cv2.resize(output_frame, (640, 360))])
            cv2.putText(pair, f"{time_s:.2f}s SOURCE", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.putText(pair, f"{time_s:.2f}s OUTPUT", (652, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imwrite(str(paired_dir / f"{scan.issue_id}_{index+1}.jpg"), pair)
    source_cap.release()
    output_cap.release()


def build_cleanup_state(
    *,
    scans: list[WindowScan],
    intervals: list[dict],
    source_path: Path,
    output_path: Path,
    source_summary: dict,
    cp07a_sha: str,
    render_sha: str,
    before_contact: Path,
    after_contact: Path,
    paired_dir: Path,
    visual_truth: dict,
    visual_qa: dict,
    audio_qa: dict,
    subtitle_qa: dict,
    media: dict,
    base_qa: dict,
    repair_iteration: int,
    cp07a_sha_after: str,
    cp07_full_timeline_path: Path,
    narration_path: Path,
    ass_path: Path,
    final_scan: dict,
) -> dict:
    issues = []
    for scan in scans:
        issues.append(
            {
                "issue_id": scan.issue_id,
                "severity": "clean",
                "category": scan.category,
                "title": scan.label,
                "stage": "delogo",
                "segment_id": scan.segment_id,
                "timestamp": round(scan.start_time, 3),
                "reviewed": False,
                "needs_review": True,
                "line_count": scan.line_count,
                "source_bbox": scan.source_bbox,
                "ocr_text": scan.text_sample,
                "detect_frame_count": len(scan.detected_frames),
                "ocr_samples": scan.ocr_samples,
            }
        )
    issue_summary = {
        "total": len(issues),
        "blockers": 0,
        "warnings": 0,
        "needs_review": len(issues),
        "reviewed": 0,
        "unresolved": len(issues),
        "clean_without_review_requirement": 0,
    }
    analysis = {
        "scan_version": "ocr_runtime_paddleocr_2.10.0_frame_scan_v1",
        "repair_iteration": repair_iteration,
        "detected_candidates": len(scans),
        "automatically_cleaned": len(scans),
        "preserved_in_scene_text": 0,
        "possible_provenance_watermark": 0,
        "unresolved_blockers": 0,
        "warnings": 0,
        "reviewed_issues": 0,
        "clean_intervals": len(intervals),
    }
    cleanup = {
        "schema_version": 1,
        "project_id": PROJECT_ID,
        "verdict": "CP08D_CLOSED_LOOP_RESIDUAL_CJK_CLEANUP_MACHINE_PASS" if final_scan["status"] == "PASS" else "CP08D_MACHINE_COMPLETE_HUMAN_REVIEW_REQUIRED",
        "machine_verdict": "PASS" if final_scan["status"] == "PASS" else "FAIL",
        "human_review_state": "REQUIRED",
        "source": {
            "path": str(source_path),
            "sha256": source_summary["sha256"],
            "duration_seconds": source_summary["media"]["duration_seconds"],
        },
        "accepted_preview_regression": {
            "path": str(Path("data/projects/vertical_slice_cp07/renders/cp07a_targeted_human_review_repair_720p.mp4")),
            "sha256_before": cp07a_sha,
            "sha256_after": cp07a_sha_after,
            "unchanged": cp07a_sha == cp07a_sha_after,
        },
        "artifact": {
            "path": str(output_path),
            "sha256": render_sha,
            "duration_seconds": media["duration_seconds"],
            "resolution": f"{media['video']['width']}x{media['video']['height']}",
        },
        "analysis": analysis,
        **analysis,
        "issue_summary": issue_summary,
        "issues": issues,
        "reviewed_issue_ids": [],
        "selected_issue_id": issues[0]["issue_id"] if issues else None,
        "approval_gate": {
            "gate_id": "cjk_cleanup_review",
            "label": "Residual CJK cleanup review",
            "state": "Pending human review",
            "approved_at": None,
            "unresolved_issue_count": len(issues),
            "action_required": "Review repaired output and confirm the preserved scene text remains acceptable.",
            "blocks_next": False,
        },
        "approvals": {"cleanup": False, "preservation": False},
        "controls": [
            {"action": "analyze_source_text_regions", "label": "Analyze source text regions"},
            {"action": "run_cleanup_pass", "label": "Run cleanup pass"},
            {"action": "scan_repaired_output", "label": "Scan repaired output"},
            {"action": "retry_selected_interval", "label": "Retry selected interval"},
            {"action": "open_next_residual_issue", "label": "Open next residual issue"},
            {"action": "previous_issue", "label": "Previous issue"},
            {"action": "seek_to_issue_timestamp", "label": "Seek to issue timestamp"},
            {"action": "view_before_after", "label": "View before/after"},
            {"action": "mark_reviewed", "label": "Mark reviewed"},
            {"action": "approve_cleanup", "label": "Approve cleanup"},
            {"action": "approve_preservation", "label": "Approve preservation"},
        ],
        "rescan": final_scan,
        "artifacts": {
            "before_contact_sheet": str(before_contact),
            "after_contact_sheet": str(after_contact),
            "paired_dir": str(paired_dir),
            "full_timeline": str(cp07_full_timeline_path),
            "narration": str(narration_path),
            "ass": str(ass_path),
        },
        "provider_calls": {"gemini": 0, "elevenlabs": 0},
        "audio_qa": audio_qa,
        "subtitle_qa": subtitle_qa,
        "visual_truth_audit": visual_truth,
        "visual_qa": visual_qa,
        "base_full_render_qa": base_qa,
        "media": media,
        "free_disk_gb": round(shutil.disk_usage(get_settings().root).free / (1024**3), 3),
    }
    cleanup["cjk_cleanup"] = build_cjk_cleanup_summary(cleanup)
    return cleanup


if __name__ == "__main__":
    main()
