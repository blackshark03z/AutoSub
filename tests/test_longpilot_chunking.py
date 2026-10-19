"""Unit tests for long-pilot chunk planning, overlap dedup and checkpoint/resume.

Synthetic-only (WP: a repeated/looped 60s clip is NEVER a long-pilot PASS). These
tests validate the deterministic stability primitives independently of any source:
chunk boundaries, overlap dedup (no lost/duplicated cues), stable ids, atomic
checkpoint writes, fail-closed corruption/master-mismatch handling, and the
source/glossary invalidation matrix.
"""
from __future__ import annotations

import json

import pytest

from app.services.longpilot_chunking import (
    CheckpointCorrupt,
    CheckpointMismatch,
    ChunkCheckpointStore,
    MasterVersion,
    merge_chunk_cues,
    plan_chunks,
    invalidation_plan,
)


def _master(sha: str = "a" * 64) -> MasterVersion:
    return MasterVersion(
        source_video_sha256=sha,
        video_width=1280,
        video_height=720,
        fps=15.0,
        duration_ms=420_000,
        ocr_engine_version="RapidOCR_PP-OCRv5_ONNX_v1.4.4",
        sampling_policy_id="sample_fps=2.0_step_ms=500",
        roi_filter_version="roi_dialogue_band_v1.2",
    )


def _cue(start, end, text, *, left=400, right=800, top=600, bottom=650, confidence=0.9):
    return {
        "start_ms": start,
        "end_ms": end,
        "text": text,
        "confidence": confidence,
        "bbox": {"left_x": left, "top_y": top, "right_x": right, "bottom_y": bottom},
    }


# ---------------------------------------------------------------------------
# Chunk planning
# ---------------------------------------------------------------------------
def test_plan_chunks_covers_timeline_with_overlap():
    windows = plan_chunks(420_000, chunk_ms=60_000, overlap_ms=2_000)
    assert windows[0].start_ms == 0
    assert windows[-1].end_ms == 420_000
    # contiguous coverage, no gap
    for prev, nxt in zip(windows, windows[1:]):
        assert prev.end_ms == nxt.start_ms
    # interior chunks have head+tail overlap for context
    assert windows[1].overlap_head_ms == 2_000
    assert windows[0].overlap_head_ms == 0
    assert windows[-1].overlap_tail_ms == 0
    # decode windows extend beyond nominal bounds by the overlap
    assert windows[1].decode_start_ms == windows[1].start_ms - 2_000
    assert windows[0].decode_end_ms == windows[0].end_ms + 2_000


def test_plan_chunks_merges_tiny_tail():
    windows = plan_chunks(123_000, chunk_ms=60_000, overlap_ms=2_000, min_tail_ms=5_000)
    assert len(windows) == 2
    assert windows[-1].end_ms == 123_000
    assert windows[-1].start_ms == 60_000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chunk_ms": 30_000},
        {"chunk_ms": 120_000},
        {"overlap_ms": 60_000, "chunk_ms": 60_000},
        {"overlap_ms": -1},
    ],
)
def test_plan_chunks_rejects_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        plan_chunks(300_000, **kwargs)


# ---------------------------------------------------------------------------
# Overlap dedup + stable ids
# ---------------------------------------------------------------------------
def test_merge_dedups_boundary_cue_without_loss_or_duplication():
    c001 = _cue(1000, 2000, "徒儿")
    c002 = _cue(60_500, 62_000, "我大限已至")  # sits in chunk0 tail / chunk1 head overlap
    c002_dup = _cue(60_600, 62_050, "我大限已至", confidence=0.95)
    c003 = _cue(120_000, 121_500, "我走后你一定要守护好宗门")
    c003_dup = _cue(120_050, 121_450, "我走后你一定要守护好宗门")

    chunks = [
        [c001, c002],
        [c002_dup, c003],
        [c003_dup],
    ]
    result = merge_chunk_cues(chunks)
    assert result["cue_count"] == 3
    assert result["duplicates_removed"] == 2
    assert [c["cue_id"] for c in result["cues"]] == ["cue_001", "cue_002", "cue_003"]
    boundary = result["cues"][1]
    assert boundary["source_chunk_indices"] == [0, 1]
    # no lost cues: the union of source texts is preserved exactly once each
    texts = [c["text"] for c in result["cues"]]
    assert sorted(texts) == sorted({"徒儿", "我大限已至", "我走后你一定要守护好宗门"})


def test_merge_keeps_longer_more_confident_representative():
    short = _cue(60_500, 61_000, "你好", confidence=0.80)
    long = _cue(60_500, 61_600, "你好", confidence=0.99)
    result = merge_chunk_cues([[short], [long]])
    assert result["cue_count"] == 1
    assert result["cues"][0]["end_ms"] == 61_600


def test_merge_distinguishes_same_text_different_time_and_different_text_same_time():
    a = _cue(1_000, 2_000, "老头", left=100, right=300)
    b = _cue(1_000, 2_000, "老头", left=900, right=1100)  # different bbox -> different cue
    c = _cue(5_000, 6_000, "老头")  # same text, far time -> different cue
    result = merge_chunk_cues([[a, b, c]])
    assert result["cue_count"] == 3


def test_merge_joins_overlapping_identical_split_cue():
    # One caption re-detected across a short tracker gap: identical text, matching
    # geometry, time overlap below 50% of the shorter cue. It is one display, so it
    # must merge into a single cue (the 233ms overlap is the diagnostic, not a merge of
    # distinct dialogue).
    first = _cue(176_133, 178_233, "跑呀怎么不跑了", left=484, right=789, top=603, bottom=648)
    second = _cue(178_000, 178_500, "跑呀怎么不跑了", left=483, right=792, top=601, bottom=647)
    result = merge_chunk_cues([[first, second]])
    assert result["cue_count"] == 1
    assert result["cues"][0]["end_ms"] == 178_500


def test_merge_keeps_non_overlapping_same_text_separate():
    a = _cue(1_000, 2_000, "好")
    b = _cue(2_500, 3_000, "好")  # same text/geometry but separate displays, no overlap
    result = merge_chunk_cues([[a, b]])
    assert result["cue_count"] == 2


def test_stable_ids_deterministic_across_input_order():
    cue_a = _cue(1_000, 2_000, "A", left=100)
    cue_b = _cue(1_000, 2_000, "B", left=500)
    forward = merge_chunk_cues([[cue_a, cue_b]])
    backward = merge_chunk_cues([[cue_b, cue_a]])
    assert [(c["cue_id"], c["text"]) for c in forward["cues"]] == [
        (c["cue_id"], c["text"]) for c in backward["cues"]
    ]


# ---------------------------------------------------------------------------
# Master version binding
# ---------------------------------------------------------------------------
def test_master_version_changes_with_source_and_geometry():
    m = _master()
    assert m.version_id == _master().version_id
    assert m.version_id != _master("b" * 64).version_id
    varied = MasterVersion(**{**_master().payload(), "video_width": 1920})
    assert varied.version_id != m.version_id


# ---------------------------------------------------------------------------
# Checkpoint / resume
# ---------------------------------------------------------------------------
def test_checkpoint_roundtrip_and_resume_skips_completed(tmp_path):
    path = tmp_path / "cp.json"
    store = ChunkCheckpointStore(path, master=_master()).load()
    windows = plan_chunks(420_000)
    for w in windows:
        store.record(w.index, w.start_ms, w.end_ms)
    store.mark(0, status="RENDERED_DONE", cue_count=3, l1_cache_key="l1x",
               translation_cache_keys={"en-US": "l3en", "vi-VN": "l3vi"},
               emitted_media={"en-US": {"path": "a.mp4", "sha256": "0" * 64}})
    store.mark(1, status="OCR_DONE", cue_count=4, l1_cache_key="l1y")
    checksum = store.save_atomic()
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))

    reloaded = ChunkCheckpointStore(path, master=_master()).load()
    plan = reloaded.resume_plan(len(windows))
    assert plan["completed"] == [0, 1]
    assert plan["resume_from"] == 2
    assert reloaded.records()[0].emitted_media["en-US"]["sha256"] == "0" * 64
    assert checksum == reloaded._serialize()["checksum"]


def test_checkpoint_reload_after_one_completed_chunk_is_idempotent(tmp_path):
    path = tmp_path / "cp.json"
    store = ChunkCheckpointStore(path, master=_master()).load()
    store.record(0, 0, 60_000)
    store.mark(0, status="TRANSLATED_DONE", cue_count=2)
    store.save_atomic()

    again = ChunkCheckpointStore(path, master=_master()).load()
    again.mark(0, status="TRANSLATED_DONE", cue_count=2)  # idempotent re-mark
    again.save_atomic()
    final = ChunkCheckpointStore(path, master=_master()).load()
    assert final.records()[0].cue_count == 2
    assert final.resume_plan(3)["completed"] == [0]


def test_checkpoint_corrupt_file_fails_closed(tmp_path):
    path = tmp_path / "cp.json"
    path.write_text("{ this is not json ", encoding="utf-8")
    with pytest.raises(CheckpointCorrupt):
        ChunkCheckpointStore(path, master=_master()).load()


def test_checkpoint_tampered_checksum_fails_closed(tmp_path):
    path = tmp_path / "cp.json"
    store = ChunkCheckpointStore(path, master=_master()).load()
    store.record(0, 0, 60_000)
    store.mark(0, status="OCR_DONE", cue_count=2)
    store.save_atomic()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["chunks"][0]["cue_count"] = 999  # tamper payload, keep old checksum
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CheckpointCorrupt):
        ChunkCheckpointStore(path, master=_master()).load()


def test_checkpoint_source_change_fails_closed(tmp_path):
    path = tmp_path / "cp.json"
    store = ChunkCheckpointStore(path, master=_master("a" * 64)).load()
    store.record(0, 0, 60_000)
    store.mark(0, status="OCR_DONE", cue_count=2)
    store.save_atomic()
    with pytest.raises(CheckpointMismatch):
        ChunkCheckpointStore(path, master=_master("b" * 64)).load()


def test_mark_rejects_unknown_field(tmp_path):
    store = ChunkCheckpointStore(tmp_path / "cp.json", master=_master()).load()
    store.record(0, 0, 60_000)
    with pytest.raises(AttributeError):
        store.mark(0, status="OCR_DONE", not_a_field=1)


# ---------------------------------------------------------------------------
# Invalidation matrix
# ---------------------------------------------------------------------------
def test_invalidation_source_change_recomputes_everything(tmp_path):
    store = ChunkCheckpointStore(tmp_path / "cp.json", master=_master("a" * 64)).load()
    plan = invalidation_plan(store, new_source_sha256="b" * 64)
    assert plan["source_changed"] is True
    assert plan["recompute_ocr"] is True
    assert plan["recompute_translation"] == {"en-US": True, "vi-VN": True}
    assert plan["recompute_media"] == {"en-US": True, "vi-VN": True}


def test_invalidation_glossary_change_is_locale_isolated(tmp_path):
    store = ChunkCheckpointStore(tmp_path / "cp.json", master=_master()).load()
    plan = invalidation_plan(store, changed_locales=["en-US"])
    assert plan["source_changed"] is False
    assert plan["recompute_ocr"] is False
    assert plan["recompute_translation"] == {"en-US": True, "vi-VN": False}
    assert plan["recompute_media"] == {"en-US": True, "vi-VN": False}
