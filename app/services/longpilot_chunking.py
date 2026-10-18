"""Long-pilot stability primitives: chunk planning, overlap dedup, atomic checkpoint/resume.

This module provides the deterministic pieces required by the US-first 5-10 minute
stability & resume pilot, independent of any particular source media:

* ``plan_chunks``          - split a source timeline into ~45-90s windows with bounded overlaps.
* ``assign_stable_ids``    - deterministic global cue ids ordered by (start_ms, left_x).
* ``merge_chunk_cues``     - deduplicate cues detected in overlapping chunk heads/tails so a
                             boundary cue is never lost or rendered twice; keeps merge provenance.
* ``MasterVersion``        - immutable identity binding for a source + OCR extraction policy.
* ``ChunkCheckpointStore`` - atomic (temp+rename) per-chunk checkpoint manifest with fail-closed
                             validation on corruption or a changed source/master identity.
* ``invalidation_plan``    - which chunks a source-hash change or a locale-glossary change invalidates.

No secrets, no absolute media paths are required by the logic itself; callers pass values in.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from app.services.multimarket_contracts import canonical_sha256

MIN_CHUNK_MS = 45_000
MAX_CHUNK_MS = 90_000
DEFAULT_CHUNK_MS = 60_000
DEFAULT_OVERLAP_MS = 2_000
MIN_TAIL_MS = 5_000

ChunkStatus = Literal["PENDING", "OCR_DONE", "TRANSLATED_DONE", "RENDERED_DONE", "FAILED"]

_SCHEMA_VERSION = "1.0.0"


class CheckpointError(Exception):
    """Base class for checkpoint failures."""


class CheckpointCorrupt(CheckpointError):
    """Raised when a checkpoint file is unreadable/tampered (fail-closed)."""


class CheckpointMismatch(CheckpointError):
    """Raised when a checkpoint was produced for a different source/master identity."""


# ---------------------------------------------------------------------------
# Chunk planning
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ChunkWindow:
    index: int
    start_ms: int
    end_ms: int
    decode_start_ms: int
    decode_end_ms: int
    overlap_head_ms: int
    overlap_tail_ms: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "decode_start_ms": self.decode_start_ms,
            "decode_end_ms": self.decode_end_ms,
            "overlap_head_ms": self.overlap_head_ms,
            "overlap_tail_ms": self.overlap_tail_ms,
        }


def plan_chunks(
    duration_ms: int,
    *,
    chunk_ms: int = DEFAULT_CHUNK_MS,
    overlap_ms: int = DEFAULT_OVERLAP_MS,
    min_tail_ms: int = MIN_TAIL_MS,
) -> List[ChunkWindow]:
    """Split ``[0, duration_ms)`` into overlapping decode windows.

    Overlap is used ONLY to give detection context; callers must dedup cues in the
    overlap using :func:`merge_chunk_cues`. A trailing window shorter than
    ``min_tail_ms`` is merged into the previous window.
    """
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    if not (MIN_CHUNK_MS <= chunk_ms <= MAX_CHUNK_MS):
        raise ValueError(f"chunk_ms must be within [{MIN_CHUNK_MS}, {MAX_CHUNK_MS}]")
    if overlap_ms < 0 or overlap_ms >= chunk_ms:
        raise ValueError("overlap_ms must be in [0, chunk_ms)")
    if min_tail_ms < 0:
        raise ValueError("min_tail_ms must be >= 0")

    windows: List[ChunkWindow] = []
    index = 0
    start = 0
    while start < duration_ms:
        end = min(start + chunk_ms, duration_ms)
        if duration_ms - start < min_tail_ms and windows:
            prev = windows[-1]
            windows[-1] = ChunkWindow(
                index=prev.index,
                start_ms=prev.start_ms,
                end_ms=duration_ms,
                decode_start_ms=prev.decode_start_ms,
                decode_end_ms=duration_ms,
                overlap_head_ms=prev.overlap_head_ms,
                overlap_tail_ms=0,
            )
            break
        head = overlap_ms if start > 0 else 0
        tail = overlap_ms if end < duration_ms else 0
        windows.append(
            ChunkWindow(
                index=index,
                start_ms=start,
                end_ms=end,
                decode_start_ms=max(0, start - head),
                decode_end_ms=min(duration_ms, end + tail),
                overlap_head_ms=head,
                overlap_tail_ms=tail,
            )
        )
        start = end
        index += 1
    return windows


# ---------------------------------------------------------------------------
# Overlap dedup + stable ids
# ---------------------------------------------------------------------------
def _bbox_dict(bbox: Any) -> Optional[Dict[str, int]]:
    if bbox is None:
        return None
    if isinstance(bbox, dict):
        if {"left_x", "top_y", "right_x", "bottom_y"} <= set(bbox):
            return {
                "left_x": int(bbox["left_x"]),
                "top_y": int(bbox["top_y"]),
                "right_x": int(bbox["right_x"]),
                "bottom_y": int(bbox["bottom_y"]),
            }
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        return {"left_x": int(bbox[0]), "top_y": int(bbox[1]), "right_x": int(bbox[2]), "bottom_y": int(bbox[3])}
    return None


def _iou(a: Optional[Dict[str, int]], b: Optional[Dict[str, int]]) -> float:
    if not a or not b:
        return 0.0
    ix = max(0, min(a["right_x"], b["right_x"]) - max(a["left_x"], b["left_x"]) + 1)
    iy = max(0, min(a["bottom_y"], b["bottom_y"]) - max(a["top_y"], b["top_y"]) + 1)
    inter = ix * iy
    if inter == 0:
        return 0.0
    area_a = (a["right_x"] - a["left_x"] + 1) * (a["bottom_y"] - a["top_y"] + 1)
    area_b = (b["right_x"] - b["left_x"] + 1) * (b["bottom_y"] - b["top_y"] + 1)
    return inter / float(area_a + area_b - inter)


def _norm_text(text: str) -> str:
    return " ".join(str(text).split())


def _time_overlap_ratio(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    lo = max(int(a["start_ms"]), int(b["start_ms"]))
    hi = min(int(a["end_ms"]), int(b["end_ms"]))
    if hi <= lo:
        return 0.0
    shorter = min(int(a["end_ms"]) - int(a["start_ms"]), int(b["end_ms"]) - int(b["start_ms"]))
    return (hi - lo) / float(shorter) if shorter > 0 else 0.0


def _same_cue(a: Dict[str, Any], b: Dict[str, Any], *, tolerance_ms: int, iou_threshold: float) -> bool:
    if _norm_text(a.get("text", a.get("zh_text", ""))) != _norm_text(b.get("text", b.get("zh_text", ""))):
        return False
    if _time_overlap_ratio(a, b) < 0.5 and abs(int(a["start_ms"]) - int(b["start_ms"])) > tolerance_ms:
        return False
    return _iou(_bbox_dict(a.get("bbox")), _bbox_dict(b.get("bbox"))) >= iou_threshold


def merge_chunk_cues(
    chunk_cues: Sequence[Sequence[Dict[str, Any]]],
    *,
    tolerance_ms: int = 250,
    iou_threshold: float = 0.5,
) -> Dict[str, Any]:
    """Deduplicate cues collected from overlapping chunks and assign stable global ids.

    Two cues are treated as the same source cue when their normalized text matches,
    their time ranges overlap OR start within ``tolerance_ms``, and their bbox IoU is
    at least ``iou_threshold``. The retained cue keeps the longer duration (tie-break:
    higher confidence, then lower start_ms, then left_x) and records the chunk indices
    that contributed it as merge provenance.
    """
    flat: List[Tuple[int, Dict[str, Any], int]] = []
    for chunk_index, cues in enumerate(chunk_cues):
        for order, cue in enumerate(cues):
            c = dict(cue)
            c.setdefault("text", c.get("zh_text", ""))
            flat.append((chunk_index, c, order))

    def _sort_key(item: Tuple[int, Dict[str, Any], int]) -> Tuple[int, int, int]:
        cue = item[1]
        left = (_bbox_dict(cue.get("bbox")) or {}).get("left_x", 0)
        return (int(cue["start_ms"]), int(left), item[2])

    flat.sort(key=_sort_key)

    kept: List[Dict[str, Any]] = []
    duplicates_removed = 0
    boundary_merges = 0

    for chunk_index, cue, _order in flat:
        match: Optional[Dict[str, Any]] = None
        for existing in kept:
            if _same_cue(existing, cue, tolerance_ms=tolerance_ms, iou_threshold=iou_threshold):
                match = existing
                break
        if match is None:
            kept.append(cue)
            cue["_chunk_indices"] = [chunk_index]
            continue

        duplicates_removed += 1
        match.setdefault("_chunk_indices", []).append(chunk_index)
        boundary_merges += 0 if len(match["_chunk_indices"]) < 2 else 1
        # keep the longer/more confident representative
        cur_dur = int(match["end_ms"]) - int(match["start_ms"])
        new_dur = int(cue["end_ms"]) - int(cue["start_ms"])
        if (new_dur, float(cue.get("confidence", 0.0))) > (cur_dur, float(match.get("confidence", 0.0))):
            merged_chunks = match["_chunk_indices"]
            cue["_chunk_indices"] = merged_chunks
            kept[kept.index(match)] = cue

    ordered = sorted(kept, key=lambda c: (int(c["start_ms"]), (_bbox_dict(c.get("bbox")) or {}).get("left_x", 0)))

    cues_out: List[Dict[str, Any]] = []
    for global_index, cue in enumerate(ordered, start=1):
        out = dict(cue)
        out.pop("_chunk_indices", None)
        out["cue_id"] = f"cue_{global_index:03d}"
        out["source_chunk_indices"] = sorted(set(cue.get("_chunk_indices", [])))
        cues_out.append(out)

    return {
        "cues": cues_out,
        "cue_count": len(cues_out),
        "duplicates_removed": duplicates_removed,
        "boundary_merges": boundary_merges,
        "chunk_count": len(chunk_cues),
    }


# ---------------------------------------------------------------------------
# Master identity
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MasterVersion:
    source_video_sha256: str
    video_width: int
    video_height: int
    fps: float
    duration_ms: int
    ocr_engine_version: str
    sampling_policy_id: str
    roi_filter_version: str
    decode_version: str = "ffmpeg9.0.1"

    def payload(self) -> Dict[str, Any]:
        return {
            "source_video_sha256": self.source_video_sha256,
            "video_width": self.video_width,
            "video_height": self.video_height,
            "fps": self.fps,
            "duration_ms": self.duration_ms,
            "ocr_engine_version": self.ocr_engine_version,
            "sampling_policy_id": self.sampling_policy_id,
            "roi_filter_version": self.roi_filter_version,
            "decode_version": self.decode_version,
        }

    @property
    def version_id(self) -> str:
        return canonical_sha256(self.payload())


# ---------------------------------------------------------------------------
# Atomic checkpoint / resume
# ---------------------------------------------------------------------------
@dataclass
class ChunkRecord:
    index: int
    source_start_ms: int
    source_end_ms: int
    status: ChunkStatus = "PENDING"
    cue_count: int = 0
    l1_cache_key: Optional[str] = None
    translation_cache_keys: Dict[str, str] = field(default_factory=dict)
    emitted_media: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    updated_at_iso: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "source_start_ms": self.source_start_ms,
            "source_end_ms": self.source_end_ms,
            "status": self.status,
            "cue_count": self.cue_count,
            "l1_cache_key": self.l1_cache_key,
            "translation_cache_keys": dict(self.translation_cache_keys),
            "emitted_media": dict(self.emitted_media),
            "updated_at_iso": self.updated_at_iso,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChunkRecord":
        return cls(
            index=int(data["index"]),
            source_start_ms=int(data["source_start_ms"]),
            source_end_ms=int(data["source_end_ms"]),
            status=data.get("status", "PENDING"),
            cue_count=int(data.get("cue_count", 0)),
            l1_cache_key=data.get("l1_cache_key"),
            translation_cache_keys=dict(data.get("translation_cache_keys", {})),
            emitted_media=dict(data.get("emitted_media", {})),
            updated_at_iso=data.get("updated_at_iso", ""),
            notes=data.get("notes", ""),
        )


_COMPLETE_STATUSES = {"OCR_DONE", "TRANSLATED_DONE", "RENDERED_DONE"}


class ChunkCheckpointStore:
    """Atomic, fail-closed per-chunk checkpoint manifest.

    The manifest is bound to a :class:`MasterVersion`. Loading a manifest whose
    stored ``master_version_id`` differs from the current one raises
    :class:`CheckpointMismatch`; a corrupt/tampered file raises
    :class:`CheckpointCorrupt`. Writes use temp-file + ``os.replace`` (atomic
    on the same volume) so an interrupted run never leaves a half-written manifest.
    """

    def __init__(self, path: Path | str, *, master: MasterVersion):
        self.path = Path(path)
        self.master = master
        self._records: Dict[int, ChunkRecord] = {}

    # -- persistence ------------------------------------------------------
    def _checksum(self, payload: Dict[str, Any]) -> str:
        body = {k: v for k, v in payload.items() if k != "checksum"}
        return canonical_sha256(body)

    def load(self) -> "ChunkCheckpointStore":
        self._records = {}
        if not self.path.exists():
            return self
        try:
            raw = self.path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointCorrupt(f"unreadable checkpoint: {self.path} ({exc})") from exc

        if not isinstance(payload, dict) or "checksum" not in payload or "master_version_id" not in payload:
            raise CheckpointCorrupt(f"malformed checkpoint: {self.path}")
        if payload["checksum"] != self._checksum(payload):
            raise CheckpointCorrupt(f"checkpoint checksum mismatch: {self.path}")
        if payload.get("master_version_id") != self.master.version_id:
            raise CheckpointMismatch(
                f"checkpoint master_version_id {payload.get('master_version_id')} != "
                f"current {self.master.version_id}"
            )
        for rec in payload.get("chunks", []):
            record = ChunkRecord.from_dict(rec)
            self._records[record.index] = record
        return self

    def _serialize(self) -> Dict[str, Any]:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "master_version_id": self.master.version_id,
            "master": self.master.payload(),
            "chunks": [self._records[i].to_dict() for i in sorted(self._records)],
        }
        payload["checksum"] = self._checksum(payload)
        return payload

    def save_atomic(self) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._serialize()
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return payload["checksum"]

    # -- mutation ---------------------------------------------------------
    def record(self, index: int, start_ms: int, end_ms: int) -> ChunkRecord:
        rec = self._records.get(index)
        if rec is None:
            rec = ChunkRecord(index=index, source_start_ms=start_ms, source_end_ms=end_ms)
            self._records[index] = rec
        return rec

    def mark(self, index: int, *, status: ChunkStatus, **fields: Any) -> ChunkRecord:
        rec = self._records[index]
        rec.status = status
        for key, value in fields.items():
            if not hasattr(rec, key):
                raise AttributeError(f"unknown ChunkRecord field: {key}")
            setattr(rec, key, value)
        rec.updated_at_iso = fields.get("updated_at_iso") or rec.updated_at_iso
        return rec

    # -- queries ----------------------------------------------------------
    def is_complete(self, index: int, *, min_status: ChunkStatus = "OCR_DONE") -> bool:
        rec = self._records.get(index)
        if rec is None:
            return False
        order = ["PENDING", "OCR_DONE", "TRANSLATED_DONE", "RENDERED_DONE"]
        if rec.status == "FAILED":
            return False
        return order.index(rec.status) >= order.index(min_status)

    def resume_plan(self, total_chunks: int, *, min_status: ChunkStatus = "OCR_DONE") -> Dict[str, Any]:
        completed = [i for i in range(total_chunks) if self.is_complete(i, min_status=min_status)]
        pending = [i for i in range(total_chunks) if i not in completed]
        return {
            "total_chunks": total_chunks,
            "completed": completed,
            "pending": pending,
            "resume_from": pending[0] if pending else None,
        }

    def records(self) -> Dict[int, ChunkRecord]:
        return dict(self._records)


def invalidation_plan(
    checkpoint: ChunkCheckpointStore,
    *,
    new_source_sha256: Optional[str] = None,
    changed_locales: Sequence[str] = (),
    locales: Sequence[str] = ("en-US", "vi-VN"),
) -> Dict[str, Any]:
    """Compute what a resume must recompute after a source change or glossary change.

    * A changed source sha invalidates everything (L1 -> L2 -> L3 -> media).
    * A changed locale glossary invalidates ONLY that locale's translation cache
      keys (and its emitted media downstream), never OCR/L1 or the other locale.
    """
    source_changed = (
        new_source_sha256 is not None and new_source_sha256 != checkpoint.master.source_video_sha256
    )
    plan: Dict[str, Any] = {
        "source_changed": source_changed,
        "changed_locales": list(changed_locales),
        "recompute_ocr": source_changed,
        "recompute_translation": {},
        "recompute_media": {},
    }
    for locale in locales:
        if source_changed:
            plan["recompute_translation"][locale] = True
            plan["recompute_media"][locale] = True
        else:
            changed = locale in changed_locales
            plan["recompute_translation"][locale] = changed
            plan["recompute_media"][locale] = changed
    return plan
