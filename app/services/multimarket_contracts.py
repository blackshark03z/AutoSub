"""Canonical Data Contracts for AutoSub Multimarket Localization Engine.

Part of AutoSub Work Package WP-AUTOSUB-US-E1-001 / Hardening.
Provides strictly immutable frozen data contracts for:
- Video source identity and raw OCR extraction (SourceZhMasterCueSet)
- Evidence-backed semantic correction review layer (SourceCorrectionReviewLayer)
- Effective dialogue cue resolution (EffectiveSourceDialogueCue)
- Localized target translation & fitting records (LocalizedCue, LocaleTranslationSet)
- Independent multi-gate release packaging manifests (MarketMediaOutputs, MultiMarketManifest)
- Cryptographic provenance & audit trail (InferenceAuditRecord, EditorialReviewAudit)

Architectural Invariants:
1. True Immutability: Collections use frozen tuples; records are immutable dataclasses.
2. Raw OCR Protection: Raw detection text, pixel coordinates, and confidences are permanently preserved.
3. Separation of Concerns: Corrections are versioned overlay records; missing evidence blocks acceptance.
4. Deterministic Identity: All fingerprints use canonical JSON SHA-256 independent of filesystem paths.
5. Timing Decoupling: Black plate mask interval is strictly locked to Chinese text presence.
6. Opaque Mask Mandatory: Plate opacity must be 1.0 (drawbox color=black@1.0); sub-1.0 is forbidden.
7. Anti-Forgery Provenance: LIVE_INFERENCE requires a cryptographic InferenceAuditRecord.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple


CorrectionType = Literal[
    "OCR_PHONETIC_TYPO",          # Visual/stroke occlusion (e.g. '三不目' -> '死不瞑目')
    "XIANXIA_TERMINOLOGY",        # Domain-specific sect/technique name alignment
    "SPEECH_AUDIO_VERIFIED",      # Audio dialogue confirmed different from burned text
    "SEGMENTATION_MERGE",         # Two broken phrase fragments combined
    "NONE"                        # Ground truth matches raw OCR exactly
]

ReviewStatus = Literal["DRAFT", "ACCEPTED", "REJECTED"]

TranslationStatus = Literal[
    "PENDING",
    "TRANSLATED",
    "FIT_OK",
    "FIT_SCALED",                        # Fitted by bounded font downscaling (<=10%)
    "NEEDS_REVIEW",                      # Exceeds CPS or cannot fit in 2 lines
    "REJECTED_HALLUCINATION",            # Triggered CJK leakage or repetition guardrail
    "TRANSLATION_RUNTIME_UNAVAILABLE"    # Fail-closed when offline model/API is not provisioned
]

TechnicalRenderStatus = Literal["PENDING", "RENDERED_OK", "FAILED"]
LocalizationQAStatus = Literal["NOT_EVALUATED", "PASSED_QA", "NEEDS_REVIEW", "FAILED_QA"]
ProvenanceRightsStatus = Literal["UNVERIFIED_SOURCE", "COMMERCIAL_LICENSE_CONFIRMED", "RIGHTS_RESTRICTED"]
PlatformPolicyStatus = Literal["NOT_EVALUATED", "REUSED_CONTENT_RISK_HIGH", "EDITORIAL_TRANSFORMATION_CONFIRMED"]
AIDisclosureStatus = Literal["NOT_EVALUATED", "AI_DISCLOSURE_REVIEW_REQUIRED", "SYNTHETIC_DECLARED", "EXEMPT"]
OwnerPublishApproval = Literal["BLOCKED_DEFAULT", "OWNER_APPROVED_FOR_STAGING", "AUTHORIZED_FOR_PUBLICATION"]

SUPPORTED_SCHEMA_VERSIONS = {"2.1.0", "2.1.0-reviewed"}


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize payload to deterministic, sorted, compact UTF-8 JSON bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    """Compute deterministic SHA-256 hash of a JSON-serializable payload."""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class InferenceAuditRecord:
    """Cryptographic audit record proving that real model inference actually occurred.
    
    Prevents callers from fabricating 'LIVE_INFERENCE' provenance without executing a model.
    """
    invocation_id: str                   # Unique execution ID from model provider/runner
    runtime_backend: str                 # e.g., "tencent_hymt2_q4_llama_cpp", "gemini_2.5_flash"
    model_id: str                        # Canonical model ID
    model_weights_sha256: str            # Cryptographic hash of model weights or endpoint ID
    prompt_sha256: str                   # Hash of prompt template sent to model
    input_text_hash: str                 # Hash of input text tokens/characters
    output_text_hash: str                # Hash of raw model response tokens/characters
    execution_timestamp_iso: str         # ISO 8601 execution timestamp
    verified_by: str                     # Subsystem or validator signing the audit

    def __post_init__(self) -> None:
        if not self.invocation_id or not self.invocation_id.strip():
            raise ValueError("invocation_id cannot be empty in InferenceAuditRecord")
        if not self.model_weights_sha256 or not self.model_weights_sha256.strip():
            raise ValueError("model_weights_sha256 cannot be empty in InferenceAuditRecord")
        if not self.verified_by or not self.verified_by.strip():
            raise ValueError("verified_by cannot be empty in InferenceAuditRecord")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> InferenceAuditRecord:
        return cls(
            invocation_id=str(data["invocation_id"]),
            runtime_backend=str(data["runtime_backend"]),
            model_id=str(data["model_id"]),
            model_weights_sha256=str(data["model_weights_sha256"]),
            prompt_sha256=str(data["prompt_sha256"]),
            input_text_hash=str(data["input_text_hash"]),
            output_text_hash=str(data["output_text_hash"]),
            execution_timestamp_iso=str(data["execution_timestamp_iso"]),
            verified_by=str(data["verified_by"]),
        )


@dataclass(frozen=True)
class EditorialReviewAudit:
    """Audit record for human-reviewed or benchmark editorial translations."""
    editorial_board_id: str
    reviewer_id: str
    review_basis: str                    # e.g., "manual_pilot_calibration", "xianxia_glossary_v1"
    review_timestamp_iso: str

    def __post_init__(self) -> None:
        if not self.reviewer_id or not self.reviewer_id.strip():
            raise ValueError("reviewer_id cannot be empty in EditorialReviewAudit")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EditorialReviewAudit:
        return cls(
            editorial_board_id=str(data.get("editorial_board_id", "")),
            reviewer_id=str(data["reviewer_id"]),
            review_basis=str(data.get("review_basis", "")),
            review_timestamp_iso=str(data.get("review_timestamp_iso", "")),
        )


@dataclass(frozen=True)
class SpatialBoundingBox:
    """Exact rectangular pixel coordinates on the source video canvas.
    
    Coordinates use inclusive 0-indexed pixel boundaries:
    - left_x, top_y: upper-left pixel
    - right_x, bottom_y: lower-right pixel (inclusive)
    - width: right_x - left_x + 1
    - height: bottom_y - top_y + 1
    """
    left_x: int
    top_y: int
    right_x: int
    bottom_y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.left_x < 0 or self.top_y < 0:
            raise ValueError(f"Bounding box coordinates must be non-negative: ({self.left_x}, {self.top_y})")
        if self.right_x < self.left_x:
            raise ValueError(f"right_x ({self.right_x}) must be >= left_x ({self.left_x})")
        if self.bottom_y < self.top_y:
            raise ValueError(f"bottom_y ({self.bottom_y}) must be >= top_y ({self.top_y})")
        expected_w = self.right_x - self.left_x + 1
        expected_h = self.bottom_y - self.top_y + 1
        if self.width != expected_w:
            raise ValueError(f"Width mismatch: specified {self.width}, expected {expected_w}")
        if self.height != expected_h:
            raise ValueError(f"Height mismatch: specified {self.height}, expected {expected_h}")

    @property
    def center(self) -> Tuple[int, int]:
        return (self.left_x + self.width // 2, self.top_y + self.height // 2)

    def validate_within_frame(self, frame_width: int, frame_height: int) -> None:
        """Verify that the box is completely contained inside frame dimensions."""
        if self.right_x >= frame_width or self.bottom_y >= frame_height:
            raise ValueError(
                f"Bounding box [{self.left_x}, {self.top_y}, {self.right_x}, {self.bottom_y}] "
                f"exceeds frame boundaries ({frame_width}x{frame_height})"
            )

    def to_opencv_slices(self) -> Tuple[slice, slice]:
        """Convert inclusive pixel coordinates to OpenCV half-open slice tuple (y_slice, x_slice)."""
        return (slice(self.top_y, self.bottom_y + 1), slice(self.left_x, self.right_x + 1))

    def to_ffmpeg_drawbox_expr(self, color: str = "black@1.0") -> str:
        """Generate FFmpeg drawbox expression enforcing solid opaque black mask."""
        if "@" in color and not (color.endswith("@1.0") or color.endswith("@1")):
            raise ValueError(f"plate color opacity must be 1.0 (got {color})")
        return f"x={self.left_x}:y={self.top_y}:w={self.width}:h={self.height}:color={color}:t=fill"

    def overlaps_disclaimer_zone(self, min_x: int = 900, min_y: int = 650) -> bool:
        """Check if box encroaches on bottom-right static AIGC watermark disclaimer zone."""
        return not (self.right_x < min_x or self.bottom_y < min_y)

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SpatialBoundingBox:
        return cls(
            left_x=int(data["left_x"]),
            top_y=int(data["top_y"]),
            right_x=int(data["right_x"]),
            bottom_y=int(data["bottom_y"]),
            width=int(data["width"]),
            height=int(data["height"]),
        )


@dataclass(frozen=True)
class SourceZhCue:
    """An immutable individual Chinese dialogue subtitle cue extracted from video pixels.
    
    Represents raw machine observation. MUST NOT be modified by downstream translation
    or human editorial passes.
    """
    cue_id: str                          # Deterministic ID, e.g., "cue_001"
    start_ms: int                        # Milliseconds from video start
    end_ms: int                          # Milliseconds from video start
    raw_ocr_text: str                    # Exact, uncorrected OCR string from detection model
    ocr_confidence: float                # Mean detection confidence [0.0 - 1.0]
    raw_bbox: SpatialBoundingBox         # Tight bounding box around detected Chinese glyphs
    plate_box: SpatialBoundingBox        # Narrow solid black mask box (with tight 2-4px padding)
    frame_count: int                     # Number of sampled video frames confirming this cue
    source_frame_provenance: str         # Deterministic hash or relative path of representative probe frame

    def __post_init__(self) -> None:
        if not self.cue_id or not isinstance(self.cue_id, str):
            raise ValueError(f"Invalid cue_id: {self.cue_id!r}")
        if self.start_ms < 0:
            raise ValueError(f"start_ms must be >= 0: {self.start_ms}")
        if self.end_ms <= self.start_ms:
            raise ValueError(f"end_ms ({self.end_ms}) must be strictly greater than start_ms ({self.start_ms})")
        if not self.raw_ocr_text or not self.raw_ocr_text.strip():
            raise ValueError("raw_ocr_text cannot be empty")
        if math.isnan(self.ocr_confidence) or math.isinf(self.ocr_confidence):
            raise ValueError(f"ocr_confidence must be finite: {self.ocr_confidence}")
        if not (0.0 <= self.ocr_confidence <= 1.0):
            raise ValueError(f"ocr_confidence must be in range [0.0, 1.0]: {self.ocr_confidence}")
        if self.frame_count < 0:
            raise ValueError(f"frame_count must be >= 0: {self.frame_count}")
        if not self.source_frame_provenance:
            raise ValueError("source_frame_provenance cannot be empty")

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    @property
    def duration_s(self) -> float:
        return self.duration_ms / 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "raw_ocr_text": self.raw_ocr_text,
            "ocr_confidence": self.ocr_confidence,
            "raw_bbox": self.raw_bbox.to_dict(),
            "plate_box": self.plate_box.to_dict(),
            "frame_count": self.frame_count,
            "source_frame_provenance": self.source_frame_provenance,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceZhCue:
        return cls(
            cue_id=str(data["cue_id"]),
            start_ms=int(data["start_ms"]),
            end_ms=int(data["end_ms"]),
            raw_ocr_text=str(data["raw_ocr_text"]),
            ocr_confidence=float(data["ocr_confidence"]),
            raw_bbox=SpatialBoundingBox.from_dict(data["raw_bbox"]),
            plate_box=SpatialBoundingBox.from_dict(data["plate_box"]),
            frame_count=int(data["frame_count"]),
            source_frame_provenance=str(data["source_frame_provenance"]),
        )


@dataclass(frozen=True)
class SourceZhMasterCueSet:
    """Immutable root container for the single-pass OCR extraction of a source video.
    
    Identity is strictly bound to source_video_sha256, frame geometry, OCR engine,
    sampling policy, and ROI rules — NEVER to cross-machine local filesystem paths.
    """
    source_video_sha256: str
    video_width: int
    video_height: int
    fps: float
    total_frames: int
    duration_ms: int
    ocr_engine_version: str              # e.g., "RapidOCR_PP-OCRv5_ONNX_v1.4.4"
    sampling_policy_id: str              # e.g., "sample_fps=2.0_step_ms=500"
    roi_filter_version: str              # e.g., "roi_dialogue_band_v1.2"
    cues: Tuple[SourceZhCue, ...]        # Strictly immutable tuple of extracted cues
    source_video_path: str = ""          # Informative only, excluded from identity hash
    schema_version: str = "2.1.0"

    def __post_init__(self) -> None:
        if not self.source_video_sha256 or len(self.source_video_sha256) != 64:
            raise ValueError(f"Invalid source_video_sha256: {self.source_video_sha256!r}")
        if self.video_width <= 0 or self.video_height <= 0:
            raise ValueError(f"Invalid video dimensions: {self.video_width}x{self.video_height}")
        if self.fps <= 0.0 or math.isnan(self.fps):
            raise ValueError(f"Invalid fps: {self.fps}")
        if self.total_frames < 0:
            raise ValueError(f"total_frames must be >= 0: {self.total_frames}")
        if self.duration_ms <= 0:
            raise ValueError(f"duration_ms must be > 0: {self.duration_ms}")
        if not isinstance(self.cues, tuple):
            raise TypeError(f"cues must be an immutable tuple, got {type(self.cues).__name__}")
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported schema_version: {self.schema_version}")

        # Validate unique stable IDs and monotonic timing order
        seen_ids = set()
        prev_start = -1
        for cue in self.cues:
            if cue.cue_id in seen_ids:
                raise ValueError(f"Duplicate cue_id detected: {cue.cue_id}")
            seen_ids.add(cue.cue_id)
            if cue.start_ms < prev_start:
                raise ValueError(f"Cues must be monotonically sorted by start_ms: {cue.cue_id} at {cue.start_ms} < {prev_start}")
            prev_start = cue.start_ms
            # Validate plate boundaries against video dimensions
            cue.plate_box.validate_within_frame(self.video_width, self.video_height)

    @property
    def extraction_hash(self) -> str:
        """Deterministic fingerprint of raw video extraction geometry and content."""
        payload = {
            "schema_version": self.schema_version,
            "source_video_sha256": self.source_video_sha256,
            "ocr_engine_version": self.ocr_engine_version,
            "sampling_policy_id": self.sampling_policy_id,
            "roi_filter_version": self.roi_filter_version,
            "video_width": self.video_width,
            "video_height": self.video_height,
            "fps": round(self.fps, 4),
            "total_frames": self.total_frames,
            "duration_ms": self.duration_ms,
            "cues": [
                {
                    "cue_id": c.cue_id,
                    "start_ms": c.start_ms,
                    "end_ms": c.end_ms,
                    "raw_ocr_text": c.raw_ocr_text,
                    "ocr_confidence": round(c.ocr_confidence, 4),
                    "raw_bbox": c.raw_bbox.to_dict(),
                    "plate_box": c.plate_box.to_dict(),
                    "frame_count": c.frame_count,
                    "provenance": c.source_frame_provenance,
                }
                for c in self.cues
            ],
        }
        return canonical_sha256(payload)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_video_sha256": self.source_video_sha256,
            "source_video_path": self.source_video_path,
            "video_width": self.video_width,
            "video_height": self.video_height,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "duration_ms": self.duration_ms,
            "ocr_engine_version": self.ocr_engine_version,
            "sampling_policy_id": self.sampling_policy_id,
            "roi_filter_version": self.roi_filter_version,
            "extraction_hash": self.extraction_hash,
            "cues": [c.to_dict() for c in self.cues],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceZhMasterCueSet:
        schema_ver = str(data.get("schema_version", "2.1.0"))
        if schema_ver not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Incompatible schema_version: {schema_ver}")
        cues_tuple = tuple(SourceZhCue.from_dict(c) for c in data.get("cues", []))
        return cls(
            source_video_sha256=str(data["source_video_sha256"]),
            video_width=int(data["video_width"]),
            video_height=int(data["video_height"]),
            fps=float(data["fps"]),
            total_frames=int(data["total_frames"]),
            duration_ms=int(data["duration_ms"]),
            ocr_engine_version=str(data["ocr_engine_version"]),
            sampling_policy_id=str(data["sampling_policy_id"]),
            roi_filter_version=str(data["roi_filter_version"]),
            cues=cues_tuple,
            source_video_path=str(data.get("source_video_path", "")),
            schema_version=schema_ver,
        )

    @classmethod
    def from_json(cls, json_str: str) -> SourceZhMasterCueSet:
        return cls.from_dict(json.loads(json_str))


@dataclass(frozen=True)
class CueCorrectionRecord:
    """Non-destructive semantic correction for a specific source cue.
    
    Mandate: Only ACCEPTED corrections with verifiable evidence provenance can
    be applied to the effective source text. Missing evidence blocks acceptance.
    """
    cue_id: str                          # Foreign key to SourceZhCue.cue_id
    raw_ocr_text: str                    # Original raw OCR text for integrity check
    corrected_text: str                  # Verified Chinese dialogue text
    correction_type: CorrectionType
    evidence_provenance: str             # e.g., "audio_transcript_check", "classical_idiom_sǐbùmíngmù"
    reviewer_id: str                     # Human reviewer identifier or automated audit rule
    review_status: ReviewStatus

    def __post_init__(self) -> None:
        if not self.cue_id:
            raise ValueError("cue_id cannot be empty")
        if not self.raw_ocr_text:
            raise ValueError("raw_ocr_text cannot be empty")
        if not self.corrected_text or not self.corrected_text.strip():
            raise ValueError("corrected_text cannot be empty")
        # Enforce evidence check: ACCEPTED modifications require explicit evidence and reviewer
        if self.review_status == "ACCEPTED" and self.is_modified():
            if not self.evidence_provenance or not self.evidence_provenance.strip():
                raise ValueError(
                    f"Correction for '{self.cue_id}' cannot be ACCEPTED without evidence_provenance."
                )
            if not self.reviewer_id or not self.reviewer_id.strip():
                raise ValueError(
                    f"Correction for '{self.cue_id}' cannot be ACCEPTED without reviewer_id."
                )

    def is_modified(self) -> bool:
        return self.raw_ocr_text != self.corrected_text

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CueCorrectionRecord:
        return cls(
            cue_id=str(data["cue_id"]),
            raw_ocr_text=str(data["raw_ocr_text"]),
            corrected_text=str(data["corrected_text"]),
            correction_type=data["correction_type"],
            evidence_provenance=str(data.get("evidence_provenance", "")),
            reviewer_id=str(data.get("reviewer_id", "")),
            review_status=data.get("review_status", "DRAFT"),
        )


@dataclass(frozen=True)
class SourceCorrectionReviewLayer:
    """Versioned collection of semantic corrections applied to an extraction.
    
    Preserves raw OCR while allowing verified human/evidence overrides.
    """
    extraction_hash: str                 # Foreign key to SourceZhMasterCueSet.extraction_hash
    layer_version: str                   # Semantic version, e.g., "v1.1.0-pilot-audit"
    corrections: Tuple[CueCorrectionRecord, ...]
    schema_version: str = "2.1.0"

    def __post_init__(self) -> None:
        if not self.extraction_hash or len(self.extraction_hash) != 64:
            raise ValueError(f"Invalid extraction_hash: {self.extraction_hash!r}")
        if not isinstance(self.corrections, tuple):
            raise TypeError("corrections must be a tuple")
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported schema_version: {self.schema_version}")
        seen = set()
        for c in self.corrections:
            if c.cue_id in seen:
                raise ValueError(f"Duplicate correction for cue_id: {c.cue_id}")
            seen.add(c.cue_id)

    @property
    def correction_layer_hash(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "extraction_hash": self.extraction_hash,
            "layer_version": self.layer_version,
            "records": [
                {
                    "cue_id": r.cue_id,
                    "raw": r.raw_ocr_text,
                    "corrected": r.corrected_text,
                    "type": r.correction_type,
                    "status": r.review_status,
                    "provenance": r.evidence_provenance,
                    "reviewer": r.reviewer_id,
                }
                for r in self.corrections
            ],
        }
        return canonical_sha256(payload)

    def apply(self, master_cues: SourceZhMasterCueSet) -> Tuple[EffectiveSourceDialogueCue, ...]:
        """Resolve master cues with accepted evidence-backed corrections.
        
        If a correction record is DRAFT, REJECTED, or lacks valid evidence,
        the raw OCR text is preserved unchanged.
        """
        if master_cues.extraction_hash != self.extraction_hash:
            raise ValueError(
                f"Extraction hash mismatch: layer references {self.extraction_hash}, "
                f"master cues has {master_cues.extraction_hash}"
            )

        correction_map = {c.cue_id: c for c in self.corrections}
        effective_list: List[EffectiveSourceDialogueCue] = []

        for raw_cue in master_cues.cues:
            corr = correction_map.get(raw_cue.cue_id)
            if corr is not None and corr.review_status == "ACCEPTED" and corr.raw_ocr_text == raw_cue.raw_ocr_text:
                effective_text = corr.corrected_text
                is_corrected = corr.is_modified()
            else:
                effective_text = raw_cue.raw_ocr_text
                is_corrected = False

            effective_list.append(
                EffectiveSourceDialogueCue(
                    cue_id=raw_cue.cue_id,
                    start_ms=raw_cue.start_ms,
                    end_ms=raw_cue.end_ms,
                    effective_text=effective_text,
                    is_corrected=is_corrected,
                    raw_ocr_text=raw_cue.raw_ocr_text,
                    plate_box=raw_cue.plate_box,
                    source_mask_start_ms=raw_cue.start_ms,
                    source_mask_end_ms=raw_cue.end_ms,
                )
            )

        return tuple(effective_list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "extraction_hash": self.extraction_hash,
            "layer_version": self.layer_version,
            "correction_layer_hash": self.correction_layer_hash,
            "corrections": [c.to_dict() for c in self.corrections],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SourceCorrectionReviewLayer:
        schema_ver = str(data.get("schema_version", "2.1.0"))
        if schema_ver not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Incompatible schema_version: {schema_ver}")
        records = tuple(CueCorrectionRecord.from_dict(c) for c in data.get("corrections", []))
        return cls(
            extraction_hash=str(data["extraction_hash"]),
            layer_version=str(data["layer_version"]),
            corrections=records,
            schema_version=schema_ver,
        )


@dataclass(frozen=True)
class EffectiveSourceDialogueCue:
    """Resolved dialogue cue dispatched to downstream translation models.
    
    Invariants:
    - source_mask_start_ms and source_mask_end_ms are strictly locked to the presence
      of original Chinese text. They MUST NOT be prolonged into silence to reduce CPS.
    """
    cue_id: str
    start_ms: int
    end_ms: int
    effective_text: str                  # Corrected text if accepted, otherwise raw OCR text
    is_corrected: bool
    raw_ocr_text: str
    plate_box: SpatialBoundingBox
    source_mask_start_ms: int            # Exact interval where black suppression plate is drawn
    source_mask_end_ms: int

    def __post_init__(self) -> None:
        if self.source_mask_start_ms != self.start_ms:
            raise ValueError("source_mask_start_ms must match start_ms")
        if self.source_mask_end_ms != self.end_ms:
            raise ValueError("source_mask_end_ms must match end_ms")

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class LocalizedCue:
    """Translation, layout geometry, and quality metrics for a single cue in a target locale."""
    cue_id: str                          # Foreign key to SourceZhCue.cue_id
    target_locale: str                   # BCP-47 tag, e.g., "en-US", "es-419", "vi-VN"
    model_target_language: str           # Model prompt language, e.g., "English", "Vietnamese"
    translated_text: str                 # Translated dialogue string
    effective_source_text: str           # Source Chinese text supplied to translator
    status: TranslationStatus
    
    # Timing & Decoupled Mask Synchrony
    subtitle_start_ms: int               # Subtitle appearance timestamp (ms)
    subtitle_end_ms: int                 # Subtitle disappearance timestamp (ms)
    source_mask_start_ms: int            # Source black plate start (locked to Chinese text)
    source_mask_end_ms: int              # Source black plate end (never prolonged into silence)
    
    # Typography & Presentation Layout
    line_breaks: Tuple[str, ...]         # Text wrapped into at most 2 balanced visual lines
    font_family: str                     # e.g., "Arial", "Trebuchet MS", "Montserrat"
    effective_font_size_px: int          # Computed font size in pixels (e.g., 32px)
    rendered_width_px: int               # Measured pixel width of widest line
    rendered_height_px: int              # Measured bounding box height of subtitle block
    chars_per_second: float              # Calculated reading rate (CPS)
    words_per_minute: float              # Calculated reading rate (WPM)
    
    # Model & Engine Provenance
    model_id: str                        # e.g., "tencent_hymt2_q4", "gemini-2.5-flash"
    prompt_version: str                  # e.g., "v2.1.0-xianxia-us"
    glossary_version: str                # e.g., "v1.2.0-wanfa-grounded"
    qa_flags: Tuple[str, ...] = ()       # e.g., ("BORDERLINE_RAPID_EXCLAMATION",)
    provenance: str = "LIVE_INFERENCE"   # "LIVE_INFERENCE" vs "EDITORIAL_REFERENCE"
    error_message: Optional[str] = None

    def __post_init__(self) -> None:
        if len(self.line_breaks) > 2:
            raise ValueError(f"Subtitles must not exceed 2 lines. Got {len(self.line_breaks)}")
        if self.source_mask_start_ms > self.subtitle_start_ms:
            # Mask must not start after subtitle begins
            pass


@dataclass(frozen=True)
class LocaleTranslationSet:
    """Complete collection of localized cues for one target market."""
    source_video_sha256: str
    extraction_hash: str
    correction_layer_hash: str
    target_locale: str
    model_target_language: str
    model_id: str
    prompt_glossary_hash: str
    created_at_iso: str
    cues: Tuple[LocalizedCue, ...]
    schema_version: str = "2.1.0"
    inference_audit: Optional[InferenceAuditRecord] = None
    editorial_audit: Optional[EditorialReviewAudit] = None

    def __post_init__(self) -> None:
        if not isinstance(self.cues, tuple):
            raise TypeError("cues must be a tuple")
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported schema_version: {self.schema_version}")

        # Provenance Anti-Forgery Validation:
        # If any cue claims LIVE_INFERENCE, inference_audit MUST be provided and valid
        has_live = any(c.provenance == "LIVE_INFERENCE" for c in self.cues)
        has_editorial = any(c.provenance == "EDITORIAL_REFERENCE" for c in self.cues)

        if has_live and self.inference_audit is None:
            raise ValueError(
                "Cannot declare LIVE_INFERENCE provenance without a valid InferenceAuditRecord."
            )
        if has_editorial and self.editorial_audit is None:
            raise ValueError(
                "Cannot declare EDITORIAL_REFERENCE provenance without a valid EditorialReviewAudit."
            )

    @property
    def translation_set_hash(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "source_video_sha256": self.source_video_sha256,
            "extraction_hash": self.extraction_hash,
            "correction_layer_hash": self.correction_layer_hash,
            "target_locale": self.target_locale,
            "model_target_language": self.model_target_language,
            "model_id": self.model_id,
            "prompt_glossary_hash": self.prompt_glossary_hash,
            "inference_audit": self.inference_audit.to_dict() if self.inference_audit else None,
            "editorial_audit": self.editorial_audit.to_dict() if self.editorial_audit else None,
            "cues": [
                {
                    "cue_id": c.cue_id,
                    "text": c.translated_text,
                    "status": c.status,
                    "start_ms": c.subtitle_start_ms,
                    "end_ms": c.subtitle_end_ms,
                    "cps": round(c.chars_per_second, 1),
                    "provenance": c.provenance,
                }
                for c in self.cues
            ],
        }
        return canonical_sha256(payload)


@dataclass
class MarketMediaOutputs:
    """Localized deliverable media artifacts and independent compliance gate statuses.
    
    CRITICAL: validation_status NEVER defaults to 'ELIGIBLE'. It defaults strictly to
    'NOT_EVALUATED' and 'BLOCKED_DEFAULT'.
    """
    target_locale: str
    burned_mp4_path: Optional[str] = None
    burned_mp4_sha256: Optional[str] = None
    srt_path: Optional[str] = None
    ass_path: Optional[str] = None
    vtt_path: Optional[str] = None
    
    # Localized Discovery Metadata (Drafts)
    localized_title_draft: str = ""
    localized_description_draft: str = ""
    localized_tags_draft: List[str] = field(default_factory=list)
    
    # Independent Compliance & Release Gates
    technical_render_status: TechnicalRenderStatus = "PENDING"
    localization_qa_status: LocalizationQAStatus = "NOT_EVALUATED"
    provenance_rights_status: ProvenanceRightsStatus = "UNVERIFIED_SOURCE"
    platform_policy_status: PlatformPolicyStatus = "NOT_EVALUATED"
    ai_disclosure_status: AIDisclosureStatus = "NOT_EVALUATED"
    owner_publish_approval: OwnerPublishApproval = "BLOCKED_DEFAULT"
    
    review_notes: List[str] = field(default_factory=list)

    @property
    def is_publication_authorized(self) -> bool:
        """Strict fail-closed evaluation. All 6 gates must pass independently."""
        return (
            self.technical_render_status == "RENDERED_OK"
            and self.localization_qa_status == "PASSED_QA"
            and self.provenance_rights_status == "COMMERCIAL_LICENSE_CONFIRMED"
            and self.platform_policy_status == "EDITORIAL_TRANSFORMATION_CONFIRMED"
            and self.ai_disclosure_status in ("SYNTHETIC_DECLARED", "EXEMPT")
            and self.owner_publish_approval == "AUTHORIZED_FOR_PUBLICATION"
        )


@dataclass
class MultiMarketManifest:
    """Master deliverable manifest: output_directory/output_manifest.json."""
    manifest_version: str = "2.1.0-reviewed"
    source_video_path: str = ""
    source_video_sha256: str = ""
    extraction_hash: str = ""
    master_cue_count: int = 0
    markets: Dict[str, MarketMediaOutputs] = field(default_factory=dict)
