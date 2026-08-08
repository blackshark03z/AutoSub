from __future__ import annotations

import pytest

from app.core.preflight import (
    MEDIA_PROCESSING_MIN_FREE_BYTES,
    PACKAGE_BUILD_MIN_FREE_BYTES,
    RUN_ONLY_MIN_FREE_BYTES,
    storage_preflight,
)


def _free(bytes_available: int):
    return lambda _path: bytes_available


@pytest.mark.parametrize(
    ("operation", "threshold"),
    [
        ("run", RUN_ONLY_MIN_FREE_BYTES),
        ("media", MEDIA_PROCESSING_MIN_FREE_BYTES),
        ("package", PACKAGE_BUILD_MIN_FREE_BYTES),
    ],
)
def test_storage_preflight_passes_exact_threshold(operation, threshold, tmp_path):
    result = storage_preflight(operation, tmp_path, free_space_getter=_free(threshold))

    assert result["passed"] is True
    assert result["current_free_bytes"] == threshold
    assert result["required_minimum_bytes"] == threshold
    assert result["margin_bytes"] == 0


@pytest.mark.parametrize(
    ("operation", "threshold"),
    [
        ("run", RUN_ONLY_MIN_FREE_BYTES),
        ("media", MEDIA_PROCESSING_MIN_FREE_BYTES),
        ("package", PACKAGE_BUILD_MIN_FREE_BYTES),
    ],
)
def test_storage_preflight_fails_below_threshold(operation, threshold, tmp_path):
    result = storage_preflight(operation, tmp_path, free_space_getter=_free(threshold - 1))

    assert result["passed"] is False
    assert result["margin_bytes"] == -1
    assert "Free at least 1 more bytes" in result["recommendation"]


def test_storage_preflight_measurement_failure_fails_closed(tmp_path):
    def boom(_path):
        raise OSError("disk probe failed")

    result = storage_preflight("run", tmp_path, free_space_getter=boom)

    assert result["passed"] is False
    assert result["current_free_bytes"] is None
    assert result["margin_bytes"] is None
    assert "failed closed" in result["recommendation"]


def test_storage_preflight_rejects_unknown_operation(tmp_path):
    with pytest.raises(ValueError):
        storage_preflight("cleanup", tmp_path)


def test_dynamic_package_gate_can_exceed_four_gib(tmp_path):
    projected = PACKAGE_BUILD_MIN_FREE_BYTES + 512
    reserve = 1024
    required = projected + reserve

    fail = storage_preflight(
        "package",
        tmp_path,
        projected_workspace_bytes=projected,
        safety_reserve_bytes=reserve,
        free_space_getter=_free(required - 1),
    )
    passed = storage_preflight(
        "package",
        tmp_path,
        projected_workspace_bytes=projected,
        safety_reserve_bytes=reserve,
        free_space_getter=_free(required),
    )

    assert fail["required_minimum_bytes"] == required
    assert fail["passed"] is False
    assert passed["passed"] is True
