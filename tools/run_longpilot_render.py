"""Long-pilot render: per-cue narrow black@1.0 mask + localized overlay for one locale.

Builds the dynamic mask plan from the real OCR boxes and the offline translations,
clamps any padded mask that grazes a protected ROI (uploader logo / AI badge) so the
badge stays intact, then renders the whole clip preserving the original audio
(stream copy). Refuses to render if any cue is unresolved.

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
    apply_protected_roi_clamp,
    build_dynamic_mask_plan,
    load_cue_boxes,
    load_render_cues,
    refit_needs_review_layouts,
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
    cues = load_render_cues(out_dir / f"translations_{args.language}.json")
    boxes = load_cue_boxes(ocr_path)
    plan = build_dynamic_mask_plan(source, cues, boxes, font_path=Path(args.font))
    pre = dict(plan["statuses"])
    plan, clamp_res = apply_protected_roi_clamp(plan)
    refit = refit_needs_review_layouts(plan, Path(args.font))
    print(f"language={args.language} cues={len(cues)} pre_clamp={pre} post_clamp={plan['statuses']}")
    print(f"clamped={clamp_res['clamped']} refit={refit} unrecoverable={clamp_res['unrecoverable']}")

    suffix = f"_{int(args.max_seconds)}s" if args.max_seconds else ""
    output = out_dir / f"longpilot_{args.language}{suffix}.mp4"
    end_s = args.max_seconds
    result = render_dynamic_mask_window(source, plan, cues, output_path=output,
                                        start_s=(0.0 if end_s else None), end_s=end_s)

    summary = {
        "language": args.language, "source": str(source), "cue_count": len(cues),
        "pre_clamp_statuses": pre, "post_clamp_statuses": plan["statuses"],
        "clamped": clamp_res["clamped"], "refit": refit, "unrecoverable": clamp_res["unrecoverable"],
        "mask_padding_px": plan["mask_padding_px"], "max_text_width": plan["max_text_width"],
        "max_mask_width_px": plan["max_mask_width_px"], "protected_rois": plan["protected_rois"],
        "output": result["output"], "ass": result["ass"],
        "cue_windows": [
            {"cue_id": l["segment_id"], "start": l["start_time"], "end": l["end_time"], "status": l["status"],
             "font_size": l["font_size"], "text_width": l["text_width"], "mask": l["mask"],
             "clamp_ops": l.get("clamp_ops"), "text": l.get("render_text")}
            for l in plan["layouts"]],
    }
    (out_dir / f"render_report_{args.language}{suffix}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"output={result['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
