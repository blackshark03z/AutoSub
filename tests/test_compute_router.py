import pytest

from app.providers.compute.base import ComputeProviderCapabilities
from app.services.compute_router import choose_compute_route
from app.services.localization_run_config import (
    COMPUTE_AUTO,
    COMPUTE_CLOUD_NO_OVERAGE,
    COMPUTE_LOCAL_ONLY,
)


def _modal(*, available=True, no_overage=True, commercial=True):
    return ComputeProviderCapabilities(
        provider="modal",
        automated=True,
        commercial_use_allowed=commercial,
        staged_artifacts=True,
        resumable=True,
        no_paid_overage_verified=no_overage,
        available=available,
    )


def test_local_only_never_routes_to_cloud() -> None:
    decision = choose_compute_route(
        policy=COMPUTE_LOCAL_ONLY,
        workload="asr",
        local_estimated_seconds=100,
        cloud_estimated_seconds=20,
        provider_capabilities=[_modal()],
    )
    assert decision.route == "local"
    assert decision.reason == "local_only_policy"


def test_auto_uses_cloud_only_when_e2e_advantage_is_material() -> None:
    fast = choose_compute_route(
        policy=COMPUTE_AUTO,
        workload="asr",
        local_estimated_seconds=100,
        cloud_estimated_seconds=60,
        provider_capabilities=[_modal()],
    )
    marginal = choose_compute_route(
        policy=COMPUTE_AUTO,
        workload="asr",
        local_estimated_seconds=100,
        cloud_estimated_seconds=90,
        provider_capabilities=[_modal()],
    )
    assert fast.route == "cloud" and fast.provider == "modal"
    assert marginal.route == "local"
    assert marginal.reason == "cloud_not_materially_faster_e2e"


def test_no_overage_policy_requires_verified_provider_spend_protection() -> None:
    decision = choose_compute_route(
        policy=COMPUTE_CLOUD_NO_OVERAGE,
        workload="ocr",
        local_estimated_seconds=200,
        cloud_estimated_seconds=20,
        provider_capabilities=[_modal(no_overage=False)],
    )
    assert decision.route == "local"
    assert decision.reason == "no_qualified_cloud_provider"


def test_auto_also_refuses_unverified_paid_overage() -> None:
    decision = choose_compute_route(
        policy=COMPUTE_AUTO,
        workload="ocr",
        local_estimated_seconds=200,
        cloud_estimated_seconds=20,
        provider_capabilities=[_modal(no_overage=False)],
    )
    assert decision.route == "local"
    assert decision.reason == "no_qualified_cloud_provider"


def test_unavailable_or_noncommercial_provider_is_not_qualified() -> None:
    for provider in (_modal(available=False), _modal(commercial=False)):
        decision = choose_compute_route(
            policy=COMPUTE_AUTO,
            workload="inpainting",
            local_estimated_seconds=200,
            cloud_estimated_seconds=20,
            provider_capabilities=[provider],
        )
        assert decision.route == "local"
        assert decision.reason == "no_qualified_cloud_provider"


def test_missing_e2e_estimates_fail_local_instead_of_guessing() -> None:
    decision = choose_compute_route(
        policy=COMPUTE_AUTO,
        workload="asr",
        local_estimated_seconds=100,
        cloud_estimated_seconds=None,
        provider_capabilities=[_modal()],
    )
    assert decision.route == "local"
    assert decision.reason == "insufficient_end_to_end_estimate"


def test_invalid_estimate_is_rejected() -> None:
    with pytest.raises(ValueError):
        choose_compute_route(
            policy=COMPUTE_AUTO,
            workload="asr",
            local_estimated_seconds=-1,
            cloud_estimated_seconds=1,
            provider_capabilities=[_modal()],
        )
