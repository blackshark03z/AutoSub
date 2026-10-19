"""Long-pilot render: per-cue adaptive union black@1.0 plate + localized overlay.

Builds the dynamic mask plan from the real OCR boxes and the offline translations.
Each plate is the tight union of the source Chinese glyph box and the measured
rendered English glyph box at source-equivalent size; plates are placed to clear the
protected ROIs (uploader logo / AI badge) so the badge stays intact. Renders the
whole clip preserving the original audio (stream copy). A cue with no usable
translation is still given a source-covering plate and reported NEEDS_REVIEW (never a
leaked caption and never a false PASS); a cue with no detected source box still fails
closed.

Usage:
    python tools/run_longpilot_render.py --out scratch/longpilot/run --language en-US
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.services.e2_dynamic_sub_mask import (  # noqa: E402
    build_dynamic_mask_plan,
    load_cue_boxes,
    load_render_cues_with_review,
    render_dynamic_mask_window,
)

FONT = Path(r"C:\Windows\Fonts\arial.ttf")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--language", required=True)
    parser.add_argument("--font", default=str(FONT))
    parser.add_argument("--max-seconds", type=float, default=None, help="bounded render window (smoke test)")
    args = parser.parse_args()

    out_dir = Path(args.out)
    ocr_path = out_dir / "ocr_audit_results.json"
    source = Path(json.loads(ocr_path.read_text(encoding="utf-8"))["video_path"])
    cues, review_only = load_render_cues_with_review(out_dir / f"translations_{args.language}.json")
    boxes = load_cue_boxes(ocr_path)
    plan = build_dynamic_mask_plan(source, cues, boxes, font_path=Path(args.font),
                                   review_only_ids=review_only)
    statuses = dict(plan["statuses"])
    print(f"language={args.language} cues={len(cues)} review_only={len(review_only)} statuses={statuses}")
    review = [l["segment_id"] for l in plan["layouts"] if l["status"] != "PASS"]
    print(f"non_pass={review}")

    suffix = f"_{int(args.max_seconds)}s" if args.max_seconds else ""
    output = out_dir / f"longpilot_{args.language}{suffix}.mp4"
    end_s = args.max_seconds
    result = render_dynamic_mask_window(source, plan, cues, output_path=output,
                                        start_s=(0.0 if end_s else None), end_s=end_s)

    def _window(l: dict) -> dict:
        char_h = l.get("source_char_height") or 0
        font_size = l.get("font_size") or 0
        mask = l.get("mask") or {}
        return {
            "cue_id": l["segment_id"], "start": l["start_time"], "end": l["end_time"], "status": l["status"],
            "font_size": font_size, "target_font_size": l.get("target_font_size"),
            "line_count": l.get("line_count"),
            "source_char_height": char_h,
            "equivalent_size_ratio": round(font_size / char_h, 3) if char_h else None,
            "text_width": l["text_width"], "text_height": l.get("text_height"),
            "plate_width": mask.get("width"), "plate_height": mask.get("height"),
            "placement_nudge_px": l.get("placement_nudge_px"),
            "covered_source_and_text": bool(
                mask and l.get("source_bbox") and l["text_width"]
                and mask.get("width", 0) >= l["text_width"]
            ),
            "mask": mask, "review_reason": l.get("review_reason"),
            "review_evidence_overlay": l.get("review_evidence_overlay"),
            "review_overlay_suppressed": l.get("review_overlay_suppressed"),
            "clamp_ops": l.get("clamp_ops"),
            "text": l.get("render_text"), "raw_text": l.get("raw_text"),
        }

    summary = {
        "language": args.language, "source": str(source), "cue_count": len(cues),
        "statuses": plan["statuses"],
        "mask_model": plan.get("mask_model"), "mask_padding_px": plan["mask_padding_px"],
        "fit_budget": plan["fit_budget"], "safe_text_width": plan.get("safe_text_width"),
        "max_nudge_px": plan.get("max_nudge_px"),
        "max_mask_width_px": plan["max_mask_width_px"], "protected_rois": plan["protected_rois"],
        "output": result["output"], "ass": result["ass"],
        "cue_windows": [_window(l) for l in plan["layouts"]],
    }
    (out_dir / f"render_report_{args.language}{suffix}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"output={result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
