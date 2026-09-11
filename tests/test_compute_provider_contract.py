from pathlib import Path

import pytest

from app.providers.compute.base import bind_compute_artifacts, build_shard_identity


SHA_A = "a" * 64
SHA_B = "b" * 64


def test_shard_identity_is_deterministic_and_parameter_order_independent() -> None:
    left = build_shard_identity(
        workload="asr",
        input_sha256=[SHA_A],
        model="openai/whisper-large-v3-turbo",
        model_revision="rev-1",
        runtime_fingerprint="modal:t4:image-1",
        parameters={"language": "zh", "beam_size": 5},
    )
    right = build_shard_identity(
        workload="asr",
        input_sha256=[SHA_A],
        model="openai/whisper-large-v3-turbo",
        model_revision="rev-1",
        runtime_fingerprint="modal:t4:image-1",
        parameters={"beam_size": 5, "language": "zh"},
    )
    assert left.shard_id == right.shard_id


def test_shard_identity_changes_when_output_affecting_inputs_change() -> None:
    base = build_shard_identity(
        workload="ocr",
        input_sha256=[SHA_A],
        model="PP-OCRv6_small",
        model_revision="rev-1",
        runtime_fingerprint="modal:t4:image-1",
        parameters={"language": "zh"},
    )
    changed_input = build_shard_identity(
        workload="ocr",
        input_sha256=[SHA_B],
        model="PP-OCRv6_small",
        model_revision="rev-1",
        runtime_fingerprint="modal:t4:image-1",
        parameters={"language": "zh"},
    )
    changed_model = build_shard_identity(
        workload="ocr",
        input_sha256=[SHA_A],
        model="PP-OCRv6_medium",
        model_revision="rev-1",
        runtime_fingerprint="modal:t4:image-1",
        parameters={"language": "zh"},
    )
    changed_runtime = build_shard_identity(
        workload="ocr",
        input_sha256=[SHA_A],
        model="PP-OCRv6_small",
        model_revision="rev-1",
        runtime_fingerprint="modal:l4:image-2",
        parameters={"language": "zh"},
    )
    assert base.shard_id != changed_input.shard_id
    assert base.shard_id != changed_model.shard_id
    assert base.shard_id != changed_runtime.shard_id


def test_shard_identity_rejects_invalid_sha() -> None:
    with pytest.raises(ValueError):
        build_shard_identity(
            workload="asr",
            input_sha256=["not-a-sha"],
            model="m",
            model_revision="r",
            runtime_fingerprint="f",
        )


def test_bind_compute_artifacts_requires_exact_hash_and_order(tmp_path: Path) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    import hashlib

    first_sha = hashlib.sha256(first.read_bytes()).hexdigest()
    second_sha = hashlib.sha256(second.read_bytes()).hexdigest()
    shard = build_shard_identity(
        workload="ocr",
        input_sha256=[first_sha, second_sha],
        model="PP-OCRv6_small",
        model_revision="rev-1",
        runtime_fingerprint="modal:t4:image-1",
    )
    artifacts = bind_compute_artifacts(shard, [first, second])
    assert [item.path for item in artifacts] == [first.resolve(), second.resolve()]
    assert [item.sha256 for item in artifacts] == [first_sha, second_sha]

    with pytest.raises(ValueError, match="hash"):
        bind_compute_artifacts(shard, [second, first])


def test_bind_compute_artifacts_rejects_missing_or_wrong_count(tmp_path: Path) -> None:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    import hashlib

    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    shard = build_shard_identity(
        workload="asr",
        input_sha256=[sha],
        model="m",
        model_revision="r",
        runtime_fingerprint="f",
    )
    with pytest.raises(ValueError, match="count"):
        bind_compute_artifacts(shard, [])
    missing = tmp_path / "missing.bin"
    with pytest.raises(FileNotFoundError):
        bind_compute_artifacts(shard, [missing])
