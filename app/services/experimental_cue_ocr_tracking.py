"""Experimental Optical Character Recognition and Subtitle Tracking Service.

Part of AutoSub Work Package WP-PILOT-002 / Relay B.
Provides independent, clean-room implementation of RapidOCR PP-OCRv5 Chinese
cue detection and temporal tracking.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass
class CueBoundingBox:
    left_x: int
    top_y: int
    right_x: int
    bottom_y: int
    width: int
    height: int

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class TrackedCue:
    cue_id: str
    start_ms: int
    end_ms: int
    start_s: float
    end_s: float
    zh_text: str
    confidence: float
    bbox: dict[str, int]
    plate_box: dict[str, int]
    frame_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Default ROI bounds for Chinese dialogue subtitles on 1280x720 video:
# Bottom band: y in [540, 690], centered horizontally: x in [180, 1100].
DEFAULT_DIALOGUE_Y_MIN = 540
DEFAULT_DIALOGUE_Y_MAX = 690
DEFAULT_DIALOGUE_X_MIN = 180
DEFAULT_DIALOGUE_X_MAX = 1100

# Subtitle-line selection (WP-M1-001 final-cue closure).
# Source captions are a single horizontally centered line at a stable baseline.
# Scene text (character cards, rank pyramids, reward lists) can touch that line at
# the same baseline, so RapidOCR sometimes emits one merged/tall box. To recover the
# true caption we select, per frame, the single-line detection nearest the run's
# dominant baseline, and bridge short unreliable frames so a continuous caption is
# not fragmented.
SUBTITLE_CENTER_TOL_RATIO = 0.07      # |center_x - frame_width/2| <= ratio * frame_width
SUBTITLE_MAX_LINE_HEIGHT_MULT = 1.5   # reject boxes taller than mult * median line height
SUBTITLE_BASELINE_BAND_RATIO = 0.035  # accept lines within ratio * frame_height of baseline
SUBTITLE_MAX_BRIDGE_MS = 1500         # max unreliable-frame gap an open cue may bridge

# Known platform disclaimer / branding keywords to exclude from subtitle cues:
STATIC_WATERMARK_KEYWORDS = (
    "AI生成",
    "视频内容由AI生成",
    "外星动漫",
    "bilibili",
    "画面内容均为AI制作",
    "请勿模仿",
)


def is_dialogue_candidate(
    box: list[list[float]] | tuple,
    text: str,
    score: float,
    frame_width: int = 1280,
    frame_height: int = 720,
    *,
    y_min_ratio: float = 0.72,
    y_max_ratio: float = 0.96,
    x_min_ratio: float = 0.12,
    x_max_ratio: float = 0.88,
    min_confidence: float = 0.65,
) -> bool:
    """Filter candidate text boxes to strictly retain centered dialogue subtitles."""
    if score < min_confidence:
        return False

    cleaned_text = text.strip()
    if not cleaned_text:
        return False

    # Check for known static watermark / disclaimer keywords
    for keyword in STATIC_WATERMARK_KEYWORDS:
        if keyword in cleaned_text:
            return False

    # Extract box extents
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    # Reject top/middle of frame (branding, upper watermark, or scene text)
    y_min_px = frame_height * y_min_ratio
    y_max_px = frame_height * y_max_ratio
    if min_y < y_min_px or max_y > y_max_px:
        return False

    # Reject far-left / far-right edge watermarks
    x_min_px = frame_width * x_min_ratio
    x_max_px = frame_width * x_max_ratio
    if min_x < x_min_px or max_x > x_max_px:
        # Bottom-right disclaimer area
        if min_x > frame_width * 0.75:
            return False
        # Bottom-left corner
        if max_x < frame_width * 0.25:
            return False

    return True


def compute_narrow_plate(
    bbox: dict[str, int],
    *,
    padding_x: int = 4,
    padding_y: int = 3,
    frame_width: int = 1280,
    frame_height: int = 720,
) -> dict[str, int]:
    """Compute a tight solid black plate bounding box covering the old subtitle."""
    left = max(0, bbox["left_x"] - padding_x)
    top = max(0, bbox["top_y"] - padding_y)
    right = min(frame_width - 1, bbox["right_x"] + padding_x)
    bottom = min(frame_height - 1, bbox["bottom_y"] + padding_y)
    return {
        "left_x": left,
        "top_y": top,
        "right_x": right,
        "bottom_y": bottom,
        "width": right - left + 1,
        "height": bottom - top + 1,
    }


def find_ocr_python_interpreter() -> Path | None:
    """Locate an available python executable with rapidocr_onnxruntime installed."""
    # 1. Environment variable override
    env_py = os.environ.get("AUTOSUB_OCR_PYTHON")
    if env_py and Path(env_py).is_file():
        return Path(env_py)

    # 2. Check local project virtualenvs
    candidates = [
        Path("scratch/ocr_env/Scripts/python.exe"),
        Path("d:/AutoSub/scratch/ocr_env/Scripts/python.exe"),
        Path(sys.executable),
    ]
    for cand in candidates:
        if cand.is_file():
            # Test if rapidocr_onnxruntime can be imported
            try:
                probe = subprocess.run(
                    [str(cand), "-c", "import rapidocr_onnxruntime"],
                    capture_output=True,
                    timeout=5,
                )
                if probe.returncode == 0:
                    return cand
            except Exception:
                continue
    return None


class ExperimentalCueOCRTracker:
    """End-to-end OCR Chinese subtitle extractor and temporal tracker."""

    def __init__(self, ocr_python: Path | str | None = None, ocr_timeout_s: float | None = None):
        self.ocr_python = Path(ocr_python) if ocr_python else find_ocr_python_interpreter()
        self._direct_engine = None
        if ocr_timeout_s is not None:
            self.ocr_timeout_s = float(ocr_timeout_s)
        else:
            try:
                self.ocr_timeout_s = float(os.environ.get("AUTOSUB_OCR_TIMEOUT_S", "900"))
            except ValueError:
                self.ocr_timeout_s = 900.0
        if not self.ocr_python and "rapidocr_onnxruntime" in sys.modules:
            from rapidocr_onnxruntime import RapidOCR
            self._direct_engine = RapidOCR()

    @property
    def is_available(self) -> bool:
        return self._direct_engine is not None or (self.ocr_python is not None and self.ocr_python.is_file())

    def run_ocr_on_frames(self, frames: list[tuple[float, np.ndarray]]) -> list[dict[str, Any]]:
        """Run OCR on a batch of (timestamp_sec, bgr_frame) tuples."""
        if not self.is_available:
            return [{"status": "OCR_RUNTIME_UNAVAILABLE", "timestamp_s": t} for t, _ in frames]

        # Use external python runner via JSON IPC if using isolated venv
        if self.ocr_python and self.ocr_python.resolve() != Path(sys.executable).resolve():
            return self._run_ocr_via_subprocess(frames)

        # In-process execution
        if self._direct_engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self._direct_engine = RapidOCR()

        frame_results = []
        for t_sec, frame in frames:
            h, w = frame.shape[:2]
            raw_res, _ = self._direct_engine(frame)
            dialogue_items = []
            if raw_res:
                for box, text, score in raw_res:
                    if is_dialogue_candidate(box, text, score, frame_width=w, frame_height=h):
                        xs = [int(p[0]) for p in box]
                        ys = [int(p[1]) for p in box]
                        dialogue_items.append({
                            "text": text.strip(),
                            "confidence": float(score),
                            "bbox": {
                                "left_x": min(xs),
                                "top_y": min(ys),
                                "right_x": max(xs),
                                "bottom_y": max(ys),
                                "width": max(xs) - min(xs) + 1,
                                "height": max(ys) - min(ys) + 1,
                            },
                        })
            frame_results.append({
                "timestamp_s": t_sec,
                "timestamp_ms": int(round(t_sec * 1000)),
                "dialogue_items": dialogue_items,
            })
        return frame_results

    def _run_ocr_via_subprocess(self, frames: list[tuple[float, np.ndarray]]) -> list[dict[str, Any]]:
        """Serialize frame images to temp files and invoke isolated OCR python interpreter."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            index_path = tmp_path / "batch.json"
            meta = []
            for idx, (t_sec, frame) in enumerate(frames):
                img_path = tmp_path / f"frame_{idx:04d}.png"
                cv2.imwrite(str(img_path), frame)
                meta.append({"index": idx, "timestamp_s": t_sec, "path": str(img_path)})
            index_path.write_text(json.dumps(meta), encoding="utf-8")

            worker_script = tmp_path / "worker.py"
            worker_code = """
import sys, json, cv2
sys.stdout.reconfigure(encoding='utf-8')
from rapidocr_onnxruntime import RapidOCR
from pathlib import Path

engine = RapidOCR()
meta = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
results = []
for item in meta:
    frame = cv2.imread(item['path'])
    if frame is None:
        results.append({'timestamp_s': item['timestamp_s'], 'raw': []})
        continue
    ocr_res, _ = engine(frame)
    out_boxes = []
    if ocr_res:
        for box, text, score in ocr_res:
            out_boxes.append({'box': box, 'text': text, 'score': float(score)})
    results.append({'timestamp_s': item['timestamp_s'], 'raw': out_boxes})
print(json.dumps(results, ensure_ascii=False))
"""
            worker_script.write_text(worker_code, encoding="utf-8")
            proc = subprocess.run(
                [str(self.ocr_python), str(worker_script), str(index_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.ocr_timeout_s,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"OCR subprocess failed: {proc.stderr}")

            raw_batch = json.loads(proc.stdout)
            frame_results = []
            for item, (t_sec, frame) in zip(raw_batch, frames):
                h, w = frame.shape[:2]
                dialogue_items = []
                for box_item in item.get("raw", []):
                    box = box_item["box"]
                    text = box_item["text"]
                    score = box_item["score"]
                    if is_dialogue_candidate(box, text, score, frame_width=w, frame_height=h):
                        xs = [int(p[0]) for p in box]
                        ys = [int(p[1]) for p in box]
                        dialogue_items.append({
                            "text": text.strip(),
                            "confidence": float(score),
                            "bbox": {
                                "left_x": min(xs),
                                "top_y": min(ys),
                                "right_x": max(xs),
                                "bottom_y": max(ys),
                                "width": max(xs) - min(xs) + 1,
                                "height": max(ys) - min(ys) + 1,
                            },
                        })
                frame_results.append({
                    "timestamp_s": t_sec,
                    "timestamp_ms": int(round(t_sec * 1000)),
                    "dialogue_items": dialogue_items,
                })
            return frame_results

    def track_video_cues(
        self,
        video_path: Path | str,
        *,
        start_s: float = 0.0,
        end_s: float | None = None,
        sample_fps: float = 2.0,
    ) -> list[TrackedCue]:
        """Track subtitle cues across a video slice."""
        video_path = Path(video_path)
        if not video_path.is_file():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps
        clip_end = min(duration, end_s) if end_s is not None else duration

        step_frames = max(1, int(round(fps / sample_fps)))
        start_frame = int(round(start_s * fps))
        end_frame = int(round(clip_end * fps))

        sampled_frames: list[tuple[float, np.ndarray]] = []
        for f_idx in range(start_frame, min(total_frames, end_frame + 1), step_frames):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if not ret:
                break
            t_sec = f_idx / fps
            sampled_frames.append((t_sec, frame))
        cap.release()

        if not sampled_frames:
            return []

        # Process in batches of 30 frames
        ocr_outputs = []
        batch_size = 30
        for i in range(0, len(sampled_frames), batch_size):
            batch = sampled_frames[i : i + batch_size]
            ocr_outputs.extend(self.run_ocr_on_frames(batch))

        frame_height, frame_width = sampled_frames[0][1].shape[:2]
        return self.aggregate_temporal_cues(
            ocr_outputs,
            frame_interval_s=1.0 / sample_fps,
            frame_width=frame_width,
            frame_height=frame_height,
        )

    @staticmethod
    def _bbox_ltrb(bbox: dict[str, Any]) -> tuple[int, int, int, int]:
        return (
            int(bbox["left_x"]),
            int(bbox["top_y"]),
            int(bbox["right_x"]),
            int(bbox["bottom_y"]),
        )

    def _dialogue_line_reference(
        self,
        items: list[dict[str, Any]],
        *,
        frame_width: int,
        frame_height: int,
    ) -> tuple[float, float]:
        """Estimate the caption line height and its dominant vertical baseline.

        Returns ``(reference_line_height, reference_center_y)`` derived from the
        horizontally centered, single-line detections of the current run.
        """
        if not items:
            return max(1.0, frame_height * 0.06), frame_height * 0.87

        heights = []
        for item in items:
            _l, top_y, _r, bottom_y = self._bbox_ltrb(item["bbox"])
            heights.append(bottom_y - top_y + 1)
        heights.sort()
        reference_line_height = float(heights[len(heights) // 2])

        center_x = frame_width / 2.0
        center_tol = SUBTITLE_CENTER_TOL_RATIO * frame_width
        max_height = reference_line_height * SUBTITLE_MAX_LINE_HEIGHT_MULT
        centers = []
        for item in items:
            left_x, top_y, right_x, bottom_y = self._bbox_ltrb(item["bbox"])
            height = bottom_y - top_y + 1
            center = (left_x + right_x) / 2.0
            if height <= max_height and abs(center - center_x) <= center_tol:
                centers.append((top_y + bottom_y) / 2.0)
        if not centers:
            return reference_line_height, frame_height * 0.87
        centers.sort()
        return reference_line_height, float(centers[len(centers) // 2])

    def _select_dialogue_line(
        self,
        items: list[dict[str, Any]],
        *,
        frame_width: int,
        frame_height: int,
        reference_line_height: float,
        reference_center_y: float,
    ) -> dict[str, Any] | None:
        """Pick the single detection that best matches the centered caption line.

        Rejects merged/multi-line boxes (too tall), off-center scene text, and lines
        far from the run's dominant baseline. Returns ``None`` when the frame carries
        no reliable caption line (so the caller bridges instead of emitting noise).
        """
        if not items:
            return None

        center_x = frame_width / 2.0
        center_tol = SUBTITLE_CENTER_TOL_RATIO * frame_width
        max_height = reference_line_height * SUBTITLE_MAX_LINE_HEIGHT_MULT
        band = SUBTITLE_BASELINE_BAND_RATIO * frame_height

        best: dict[str, Any] | None = None
        best_key: tuple[float, float, float] | None = None
        for item in items:
            left_x, top_y, right_x, bottom_y = self._bbox_ltrb(item["bbox"])
            height = bottom_y - top_y + 1
            if height > max_height:
                continue
            center = (left_x + right_x) / 2.0
            if abs(center - center_x) > center_tol:
                continue
            center_y = (top_y + bottom_y) / 2.0
            if abs(center_y - reference_center_y) > band:
                continue
            key = (abs(center_y - reference_center_y), -float(bottom_y), abs(center - center_x))
            if best_key is None or key < best_key:
                best_key = key
                best = item
        return best

    def aggregate_temporal_cues(
        self,
        frame_detections: list[dict[str, Any]],
        frame_interval_s: float = 0.5,
        *,
        frame_width: int = 1280,
        frame_height: int = 720,
    ) -> list[TrackedCue]:
        """Aggregate frame-level detections into discrete, stable temporal subtitle cues.

        Per frame the true caption line is recovered via :meth:`_select_dialogue_line`.
        Frames that carry no reliable caption line but still contain detected text are
        "bridged": an open cue is extended through them (bounded by
        ``SUBTITLE_MAX_BRIDGE_MS``) so a continuous caption is not fragmented. Frames
        with no detections at all are treated as true silence and close the cue.
        """
        cues: list[TrackedCue] = []
        all_items = [item for fd in frame_detections for item in fd.get("dialogue_items", [])]
        reference_line_height, reference_center_y = self._dialogue_line_reference(
            all_items, frame_width=frame_width, frame_height=frame_height
        )

        current_cue_frames: list[dict[str, Any]] = []
        current_end_s: float | None = None
        last_reliable_s: float | None = None

        def _flush() -> None:
            nonlocal current_cue_frames, current_end_s, last_reliable_s
            if current_cue_frames:
                cue = self._build_cue_from_frames(
                    current_cue_frames, frame_interval_s, cue_idx=len(cues) + 1, end_s=current_end_s
                )
                if cue:
                    cues.append(cue)
            current_cue_frames = []
            current_end_s = None
            last_reliable_s = None

        for frame_data in frame_detections:
            items = frame_data.get("dialogue_items", [])
            t_s = frame_data["timestamp_s"]

            if not items:
                # True silence / no text at all: close any open cue.
                _flush()
                continue

            selected = self._select_dialogue_line(
                items,
                frame_width=frame_width,
                frame_height=frame_height,
                reference_line_height=reference_line_height,
                reference_center_y=reference_center_y,
            )

            if selected is None:
                # Detected text, but no reliable caption line: bridge the gap.
                if current_cue_frames and last_reliable_s is not None:
                    if (t_s - last_reliable_s) * 1000.0 <= SUBTITLE_MAX_BRIDGE_MS:
                        current_end_s = t_s + frame_interval_s
                    else:
                        _flush()
                continue

            text = selected["text"]
            if not current_cue_frames:
                current_cue_frames.append({"timestamp_s": t_s, "item": selected})
            elif self._texts_match(current_cue_frames[-1]["item"]["text"], text):
                current_cue_frames.append({"timestamp_s": t_s, "item": selected})
            else:
                _flush()
                current_cue_frames.append({"timestamp_s": t_s, "item": selected})
            last_reliable_s = t_s
            current_end_s = t_s + frame_interval_s

        _flush()
        return cues

    @staticmethod
    def _texts_match(text1: str, text2: str) -> bool:
        """Check if two consecutive OCR strings represent the same spoken sentence."""
        if text1 == text2:
            return True
        # Character overlap ratio
        set1, set2 = set(text1), set(text2)
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        return (intersection / union) >= 0.75 if union else False

    @staticmethod
    def _build_cue_from_frames(
        frame_items: list[dict[str, Any]],
        frame_interval_s: float,
        cue_idx: int,
        end_s: float | None = None,
    ) -> TrackedCue | None:
        if not frame_items:
            return None

        start_s = frame_items[0]["timestamp_s"]
        if end_s is None:
            end_s = frame_items[-1]["timestamp_s"] + frame_interval_s

        boxes = [f["item"]["bbox"] for f in frame_items]
        left_x = min(b["left_x"] for b in boxes)
        top_y = min(b["top_y"] for b in boxes)
        right_x = max(b["right_x"] for b in boxes)
        bottom_y = max(b["bottom_y"] for b in boxes)

        bbox = {
            "left_x": left_x,
            "top_y": top_y,
            "right_x": right_x,
            "bottom_y": bottom_y,
            "width": right_x - left_x + 1,
            "height": bottom_y - top_y + 1,
        }
        plate_box = compute_narrow_plate(bbox, padding_x=4, padding_y=3)

        # Most representative text (longest string or latest stable)
        texts = [f["item"]["text"] for f in frame_items]
        representative_text = max(texts, key=len)
        confidences = [f["item"]["confidence"] for f in frame_items]
        avg_confidence = round(sum(confidences) / len(confidences), 3)

        return TrackedCue(
            cue_id=f"cue_{cue_idx:03d}",
            start_ms=int(round(start_s * 1000)),
            end_ms=int(round(end_s * 1000)),
            start_s=round(start_s, 3),
            end_s=round(end_s, 3),
            zh_text=representative_text,
            confidence=avg_confidence,
            bbox=bbox,
            plate_box=plate_box,
            frame_count=len(frame_items),
        )


def build_ffmpeg_drawbox_filter(cue: TrackedCue) -> str:
    """Generate an FFmpeg drawbox expression for a single tracked cue."""
    p = cue.plate_box
    return (
        f"drawbox=x={p['left_x']}:y={p['top_y']}:w={p['width']}:h={p['height']}:"
        f"color=black@1.0:t=fill:enable='between(t,{cue.start_s:.3f},{cue.end_s:.3f})'"
    )
