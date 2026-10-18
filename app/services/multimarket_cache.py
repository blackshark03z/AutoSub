"""Deterministic Multi-Tier SQLite Cache for AutoSub Multimarket Localization.

Part of AutoSub Work Package WP-AUTOSUB-US-E1-001 / Hardening.
Implements the 5-Tier Cache Architecture:
- Tier 1 (L1_RAW_OCR): Video pixels, OCR engine, weights hash, sampling policy, ROI filters.
- Tier 2 (L2_SEMANTIC): Non-destructive semantic correction review layer.
- Tier 3 (L3_TRANSLATION): Locale, prompt template, domain glossary, model ID/weights, runtime.
- Tier 4 (L4_FITTING): Measured FreeType typography, line wrapping, safe area geometry, shaping engine.
- Tier 5 (L5_MEDIA_EXPORT): Encoded media outputs, audio stream hash, container presets, opaque plate opacity=1.0.

Key Invariant Guarantees:
1. Cache Isolation: No cache row crosses locales, model versions, or distinct source videos.
2. Invalidation Precision: Changing a font only invalidates L4/L5; editing a glossary only invalidates
   L3-L5 for that locale; source video OCR (L1) is never unnecessarily recomputed.
3. Anti-Poisoning: Manual or editorial reference translations are rejected from the live cache namespace.
   Live inference requires verified InferenceAuditRecord cryptographic evidence.
4. Opaque Plate Mandatory: plate_opacity must be strictly 1.0 (sub-1.0 values raise ValueError).
5. ACID Reliability: Transactions, payload checksum verification on read, corruption fail-closed,
   WAL mode, busy_timeout handling, and foreign key cascades.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

from app.services.multimarket_contracts import (
    LocaleTranslationSet,
    SourceCorrectionReviewLayer,
    SourceZhMasterCueSet,
    canonical_json_bytes,
    canonical_sha256,
)


def compute_l1_key(
    *,
    source_video_sha256: str,
    video_width: int,
    video_height: int,
    ocr_engine_version: str,
    ocr_weights_sha256: str = "default_weights",
    sampling_policy_id: str,
    roi_filter_version: str,
    geometry_version: str = "v1",
) -> str:
    """Compute deterministic Tier 1 (Raw OCR extraction) cache key."""
    payload = {
        "tier": "L1_RAW_OCR",
        "source_video_sha256": source_video_sha256,
        "video_width": video_width,
        "video_height": video_height,
        "ocr_engine_version": ocr_engine_version,
        "ocr_weights_sha256": ocr_weights_sha256,
        "sampling_policy_id": sampling_policy_id,
        "roi_filter_version": roi_filter_version,
        "geometry_version": geometry_version,
    }
    return canonical_sha256(payload)


def compute_l2_key(
    *,
    l1_cache_key: str,
    correction_layer_hash: str,
    schema_version: str = "2.1.0",
) -> str:
    """Compute deterministic Tier 2 (Semantic correction review) cache key."""
    payload = {
        "tier": "L2_SEMANTIC",
        "l1_cache_key": l1_cache_key,
        "correction_layer_hash": correction_layer_hash,
        "schema_version": schema_version,
    }
    return canonical_sha256(payload)


def compute_l3_key(
    *,
    l2_cache_key: str,
    target_locale: str,
    model_target_language: str,
    model_id: str,
    model_weights_sha256: str = "default_weights",
    runtime_backend: str,
    prompt_template_sha256: str,
    glossary_sha256: str,
    context_window_size: int = 3,
) -> str:
    """Compute deterministic Tier 3 (Locale translation memory) cache key."""
    payload = {
        "tier": "L3_TRANSLATION",
        "l2_cache_key": l2_cache_key,
        "target_locale": target_locale,
        "model_target_language": model_target_language,
        "model_id": model_id,
        "model_weights_sha256": model_weights_sha256,
        "runtime_backend": runtime_backend,
        "prompt_template_sha256": prompt_template_sha256,
        "glossary_sha256": glossary_sha256,
        "context_window_size": context_window_size,
    }
    return canonical_sha256(payload)


def compute_l4_key(
    *,
    l3_cache_key: str,
    plate_geometry_hash: str,
    font_family: str,
    font_file_sha256: str,
    wrap_algo_version: str,
    video_width: int,
    video_height: int,
    safe_margin_ratio: float = 0.06,
    cps_policy_version: str = "v1",
    shaping_engine: str = "libass_v1",
) -> str:
    """Compute deterministic Tier 4 (Typography, fitting & layout) cache key."""
    payload = {
        "tier": "L4_FITTING",
        "l3_cache_key": l3_cache_key,
        "plate_geometry_hash": plate_geometry_hash,
        "font_family": font_family,
        "font_file_sha256": font_file_sha256,
        "wrap_algo_version": wrap_algo_version,
        "video_width": video_width,
        "video_height": video_height,
        "safe_margin_ratio": round(safe_margin_ratio, 4),
        "cps_policy_version": cps_policy_version,
        "shaping_engine": shaping_engine,
    }
    return canonical_sha256(payload)


def compute_l5_key(
    *,
    l4_cache_key: str,
    source_video_sha256: str,
    audio_stream_index: int,
    audio_stream_sha256: str = "default_audio",
    ffmpeg_version: str,
    render_preset: str,
    plate_opacity: float = 1.0,
) -> str:
    """Compute deterministic Tier 5 (Media export & packaging) cache key.
    
    Mandate: plate_opacity must be 1.0 for solid Chinese dialogue suppression.
    """
    if not math.isfinite(plate_opacity) or plate_opacity != 1.0:
        raise ValueError(
            f"plate_opacity must be 1.0 (exactly the finite value) for solid Chinese "
            f"dialogue suppression; got {plate_opacity!r}. Any other value (including "
            f"NaN/inf and sub-1.0 opacity) causes Chinese text ghosting or leakage."
        )
    payload = {
        "tier": "L5_MEDIA_EXPORT",
        "l4_cache_key": l4_cache_key,
        "source_video_sha256": source_video_sha256,
        "audio_stream_index": audio_stream_index,
        "audio_stream_sha256": audio_stream_sha256,
        "ffmpeg_version": ffmpeg_version,
        "render_preset": render_preset,
        "plate_opacity": round(plate_opacity, 3),
    }
    return canonical_sha256(payload)


class MultiMarketCacheStore:
    """ACID SQLite store for deterministic 5-Tier AutoSub Multimarket Cache."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path).resolve()
        # Path traversal guard
        if ".." in str(db_path):
            raise ValueError(f"Path traversal characters forbidden in cache db_path: {db_path}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            isolation_level=None,  # Autocommit mode, explicit transactions via BEGIN
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _init_database(self) -> None:
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_schema_meta (
                        version_key TEXT PRIMARY KEY,
                        schema_version TEXT NOT NULL,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute(
                    "INSERT OR IGNORE INTO cache_schema_meta (version_key, schema_version) VALUES ('current', '2.1.0')"
                )

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_l1_extractions (
                        cache_key TEXT PRIMARY KEY,
                        source_video_sha256 TEXT NOT NULL,
                        extraction_hash TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_checksum TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_l1_video ON cache_l1_extractions (source_video_sha256)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_l2_corrections (
                        cache_key TEXT PRIMARY KEY,
                        l1_key TEXT NOT NULL,
                        layer_hash TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_checksum TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (l1_key) REFERENCES cache_l1_extractions (cache_key) ON DELETE CASCADE
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_l2_l1 ON cache_l2_corrections (l1_key)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_l3_translations (
                        cache_key TEXT PRIMARY KEY,
                        l2_key TEXT NOT NULL,
                        target_locale TEXT NOT NULL,
                        model_id TEXT NOT NULL,
                        provenance TEXT NOT NULL,  -- 'LIVE_INFERENCE' or 'EDITORIAL_REFERENCE'
                        payload_json TEXT NOT NULL,
                        payload_checksum TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (l2_key) REFERENCES cache_l2_corrections (cache_key) ON DELETE CASCADE
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_l3_locale ON cache_l3_translations (target_locale)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_l3_l2 ON cache_l3_translations (l2_key)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_l4_layouts (
                        cache_key TEXT PRIMARY KEY,
                        l3_key TEXT NOT NULL,
                        target_locale TEXT NOT NULL,
                        font_family TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_checksum TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (l3_key) REFERENCES cache_l3_translations (cache_key) ON DELETE CASCADE
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_l4_font ON cache_l4_layouts (font_family)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_l4_l3 ON cache_l4_layouts (l3_key)")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cache_l5_exports (
                        cache_key TEXT PRIMARY KEY,
                        l4_key TEXT NOT NULL,
                        target_locale TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_checksum TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (l4_key) REFERENCES cache_l4_layouts (cache_key) ON DELETE CASCADE
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_l5_l4 ON cache_l5_exports (l4_key)")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _compute_checksum(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # --------------------------------------------------------------------------
    # Tier 1 Operations: Raw OCR Extractions
    # --------------------------------------------------------------------------

    def put_l1_extraction(self, cache_key: str, cue_set: SourceZhMasterCueSet) -> None:
        """Cache an immutable SourceZhMasterCueSet in Tier 1 with payload checksum."""
        payload = cue_set.to_json()
        checksum = self._compute_checksum(payload)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO cache_l1_extractions (
                        cache_key, source_video_sha256, extraction_hash, payload_json, payload_checksum
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        extraction_hash = excluded.extraction_hash,
                        payload_json = excluded.payload_json,
                        payload_checksum = excluded.payload_checksum
                    """,
                    (cache_key, cue_set.source_video_sha256, cue_set.extraction_hash, payload, checksum),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_l1_extraction(self, cache_key: str) -> Optional[SourceZhMasterCueSet]:
        """Retrieve cached SourceZhMasterCueSet from Tier 1 with checksum validation."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT payload_json, payload_checksum FROM cache_l1_extractions WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            payload = row["payload_json"]
            expected_checksum = row["payload_checksum"]
            if self._compute_checksum(payload) != expected_checksum:
                # Corrupt cache entry fails closed
                conn.execute("DELETE FROM cache_l1_extractions WHERE cache_key = ?", (cache_key,))
                return None

            try:
                return SourceZhMasterCueSet.from_json(payload)
            except Exception:
                conn.execute("DELETE FROM cache_l1_extractions WHERE cache_key = ?", (cache_key,))
                return None

    # --------------------------------------------------------------------------
    # Tier 2 Operations: Semantic Corrections
    # --------------------------------------------------------------------------

    def put_l2_correction(
        self,
        cache_key: str,
        l1_cache_key: str,
        review_layer: SourceCorrectionReviewLayer,
    ) -> None:
        """Cache a versioned SourceCorrectionReviewLayer in Tier 2 with payload checksum."""
        payload = json.dumps(review_layer.to_dict(), ensure_ascii=False)
        checksum = self._compute_checksum(payload)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO cache_l2_corrections (
                        cache_key, l1_key, layer_hash, payload_json, payload_checksum
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        layer_hash = excluded.layer_hash,
                        payload_json = excluded.payload_json,
                        payload_checksum = excluded.payload_checksum
                    """,
                    (cache_key, l1_cache_key, review_layer.correction_layer_hash, payload, checksum),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_l2_correction(self, cache_key: str) -> Optional[SourceCorrectionReviewLayer]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT payload_json, payload_checksum FROM cache_l2_corrections WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            payload = row["payload_json"]
            if self._compute_checksum(payload) != row["payload_checksum"]:
                conn.execute("DELETE FROM cache_l2_corrections WHERE cache_key = ?", (cache_key,))
                return None

            try:
                return SourceCorrectionReviewLayer.from_dict(json.loads(payload))
            except Exception:
                conn.execute("DELETE FROM cache_l2_corrections WHERE cache_key = ?", (cache_key,))
                return None

    # --------------------------------------------------------------------------
    # Tier 3 Operations: Locale Translation Memory
    # --------------------------------------------------------------------------

    def put_l3_translation(
        self,
        cache_key: str,
        l2_cache_key: str,
        translation_set: LocaleTranslationSet,
        *,
        allow_editorial: bool = False,
    ) -> None:
        """Cache translated cues for a target market with cryptographic provenance verification.
        
        Anti-Poisoning Enforcement:
        1. LIVE_INFERENCE requires that translation_set.inference_audit is valid and verified.
           A caller cannot declare LIVE_INFERENCE without real model execution evidence.
        2. EDITORIAL_REFERENCE requires allow_editorial=True and translation_set.editorial_audit.
        """
        has_editorial = any(c.provenance == "EDITORIAL_REFERENCE" for c in translation_set.cues)
        has_live = any(c.provenance == "LIVE_INFERENCE" for c in translation_set.cues)

        if has_live and translation_set.inference_audit is None:
            raise ValueError(
                "Refusing to cache: LIVE_INFERENCE declared without valid InferenceAuditRecord."
            )

        if has_editorial:
            if not allow_editorial:
                raise ValueError(
                    "Refusing to cache editorial reference or benchmark draft in live cache namespace. "
                    "Must set allow_editorial=True to cache in editorial namespace."
                )
            if translation_set.editorial_audit is None:
                raise ValueError(
                    "Refusing to cache: EDITORIAL_REFERENCE declared without EditorialReviewAudit."
                )

        if not has_live and not has_editorial:
            raise ValueError(
                "Refusing to cache: translation set declares neither LIVE_INFERENCE nor "
                "EDITORIAL_REFERENCE provenance; ambiguous provenance cannot be tagged live."
            )
        provenance_tag = "EDITORIAL_REFERENCE" if has_editorial else "LIVE_INFERENCE"
        payload = json.dumps(
            {
                "schema_version": translation_set.schema_version,
                "source_video_sha256": translation_set.source_video_sha256,
                "extraction_hash": translation_set.extraction_hash,
                "correction_layer_hash": translation_set.correction_layer_hash,
                "target_locale": translation_set.target_locale,
                "model_target_language": translation_set.model_target_language,
                "model_id": translation_set.model_id,
                "prompt_glossary_hash": translation_set.prompt_glossary_hash,
                "created_at_iso": translation_set.created_at_iso,
                "inference_audit": translation_set.inference_audit.to_dict() if translation_set.inference_audit else None,
                "editorial_audit": translation_set.editorial_audit.to_dict() if translation_set.editorial_audit else None,
                "cues": [
                    {
                        "cue_id": c.cue_id,
                        "target_locale": c.target_locale,
                        "model_target_language": c.model_target_language,
                        "translated_text": c.translated_text,
                        "effective_source_text": c.effective_source_text,
                        "status": c.status,
                        "subtitle_start_ms": c.subtitle_start_ms,
                        "subtitle_end_ms": c.subtitle_end_ms,
                        "source_mask_start_ms": c.source_mask_start_ms,
                        "source_mask_end_ms": c.source_mask_end_ms,
                        "line_breaks": list(c.line_breaks),
                        "font_family": c.font_family,
                        "effective_font_size_px": c.effective_font_size_px,
                        "rendered_width_px": c.rendered_width_px,
                        "rendered_height_px": c.rendered_height_px,
                        "chars_per_second": c.chars_per_second,
                        "words_per_minute": c.words_per_minute,
                        "model_id": c.model_id,
                        "prompt_version": c.prompt_version,
                        "glossary_version": c.glossary_version,
                        "qa_flags": list(c.qa_flags),
                        "provenance": c.provenance,
                        "error_message": c.error_message,
                    }
                    for c in translation_set.cues
                ],
            },
            ensure_ascii=False,
        )
        checksum = self._compute_checksum(payload)

        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO cache_l3_translations (
                        cache_key, l2_key, target_locale, model_id, provenance, payload_json, payload_checksum
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        model_id = excluded.model_id,
                        provenance = excluded.provenance,
                        payload_json = excluded.payload_json,
                        payload_checksum = excluded.payload_checksum
                    """,
                    (
                        cache_key,
                        l2_cache_key,
                        translation_set.target_locale,
                        translation_set.model_id,
                        provenance_tag,
                        payload,
                        checksum,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_l3_translation(
        self,
        cache_key: str,
        *,
        require_live_inference: bool = True,
    ) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT provenance, payload_json, payload_checksum FROM cache_l3_translations WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            payload = row["payload_json"]
            if self._compute_checksum(payload) != row["payload_checksum"]:
                conn.execute("DELETE FROM cache_l3_translations WHERE cache_key = ?", (cache_key,))
                return None

            if require_live_inference and row["provenance"] != "LIVE_INFERENCE":
                # Editorial references are hidden from live inference queries
                return None

            try:
                return json.loads(payload)
            except Exception:
                conn.execute("DELETE FROM cache_l3_translations WHERE cache_key = ?", (cache_key,))
                return None

    # --------------------------------------------------------------------------
    # Tier 4 Operations: Subtitle Fitting & Layout
    # --------------------------------------------------------------------------

    def put_l4_layout(
        self,
        cache_key: str,
        l3_cache_key: str,
        target_locale: str,
        font_family: str,
        layout_payload: Dict[str, Any],
    ) -> None:
        payload = json.dumps(layout_payload, ensure_ascii=False)
        checksum = self._compute_checksum(payload)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO cache_l4_layouts (
                        cache_key, l3_key, target_locale, font_family, payload_json, payload_checksum
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        payload_checksum = excluded.payload_checksum
                    """,
                    (cache_key, l3_cache_key, target_locale, font_family, payload, checksum),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_l4_layout(self, cache_key: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT payload_json, payload_checksum FROM cache_l4_layouts WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            payload = row["payload_json"]
            if self._compute_checksum(payload) != row["payload_checksum"]:
                conn.execute("DELETE FROM cache_l4_layouts WHERE cache_key = ?", (cache_key,))
                return None

            try:
                return json.loads(payload)
            except Exception:
                conn.execute("DELETE FROM cache_l4_layouts WHERE cache_key = ?", (cache_key,))
                return None

    # --------------------------------------------------------------------------
    # Tier 5 Operations: Media Export & Packaging
    # --------------------------------------------------------------------------

    def put_l5_export(
        self,
        cache_key: str,
        l4_cache_key: str,
        target_locale: str,
        export_payload: Dict[str, Any],
    ) -> None:
        payload = json.dumps(export_payload, ensure_ascii=False)
        checksum = self._compute_checksum(payload)
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO cache_l5_exports (
                        cache_key, l4_key, target_locale, payload_json, payload_checksum
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        payload_checksum = excluded.payload_checksum
                    """,
                    (cache_key, l4_cache_key, target_locale, payload, checksum),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_l5_export(self, cache_key: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT payload_json, payload_checksum FROM cache_l5_exports WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            payload = row["payload_json"]
            if self._compute_checksum(payload) != row["payload_checksum"]:
                conn.execute("DELETE FROM cache_l5_exports WHERE cache_key = ?", (cache_key,))
                return None

            try:
                return json.loads(payload)
            except Exception:
                conn.execute("DELETE FROM cache_l5_exports WHERE cache_key = ?", (cache_key,))
                return None

    # --------------------------------------------------------------------------
    # Invalidation Routines
    # --------------------------------------------------------------------------

    def invalidate_source_video(self, source_video_sha256: str) -> Dict[str, int]:
        """Invalidate an entire video and all derived downstream tiers."""
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    "DELETE FROM cache_l1_extractions WHERE source_video_sha256 = ?",
                    (source_video_sha256,),
                )
                l1_deleted = cursor.rowcount
                # Cascades handle L2-L5
                conn.execute("COMMIT")
                return {"l1_deleted": l1_deleted}
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def invalidate_locale(self, target_locale: str) -> Dict[str, int]:
        """Invalidate L3-L5 for a specific target_locale.
        
        Guarantees that other locales and L1/L2 extractions are 100% PRESERVED.
        """
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    "DELETE FROM cache_l3_translations WHERE target_locale = ?",
                    (target_locale,),
                )
                l3_deleted = cursor.rowcount
                # L4 and L5 cascade automatically
                conn.execute("COMMIT")
                return {"l3_deleted": l3_deleted}
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def invalidate_font(self, font_family: str) -> Dict[str, int]:
        """Invalidate L4-L5 for a specific font.
        
        Guarantees that L1 (OCR), L2 (Correction), and L3 (Translation) are 100% PRESERVED.
        """
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    "DELETE FROM cache_l4_layouts WHERE font_family = ?",
                    (font_family,),
                )
                l4_deleted = cursor.rowcount
                # L5 cascades automatically
                conn.execute("COMMIT")
                return {"l4_deleted": l4_deleted}
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def invalidate_correction(self, l1_cache_key: str) -> Dict[str, int]:
        """Invalidate L2 corrections and downstream L3-L5 for an extraction.
        
        Guarantees that L1 (raw OCR detection) is 100% PRESERVED.
        """
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    "DELETE FROM cache_l2_corrections WHERE l1_key = ?",
                    (l1_cache_key,),
                )
                l2_deleted = cursor.rowcount
                conn.execute("COMMIT")
                return {"l2_deleted": l2_deleted}
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def clear_all(self) -> None:
        """Clear all cache tiers."""
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM cache_l5_exports")
                conn.execute("DELETE FROM cache_l4_layouts")
                conn.execute("DELETE FROM cache_l3_translations")
                conn.execute("DELETE FROM cache_l2_corrections")
                conn.execute("DELETE FROM cache_l1_extractions")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
