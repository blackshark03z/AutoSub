from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.providers.compute.base import ComputeProviderCapabilities
from app.services.localization_run_config import (
    COMPUTE_AUTO,
    COMPUTE_CLOUD_NO_OVERAGE,
    COMPUTE_LOCAL_ONLY,
)


@dataclass(frozen=True)
class ComputeRouteDecision:
    route: str
    provider: str | None
    reason: str
    local_estimated_seconds: float | None
    cloud_estimated_seconds: float | None


def _qualified(capability: ComputeProviderCapabilities, *, require_no_overage: bool) -> bool:
    return bool(
        capability.available
        and capability.automated
        and capability.commercial_use_allowed
        and capability.staged_artifacts
        and capability.resumable
        and (capability.no_paid_overage_verified or not require_no_overage)
    )


def _safe_seconds(value: float | int | None) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if parsed < 0:
        raise ValueError("estimated seconds cannot be negative")
    return parsed


def choose_compute_route(
    *,
    policy: str,
    workload: str,
    local_estimated_seconds: float | int | None,
    cloud_estimated_seconds: float | int | None,
    provider_capabilities: Iterable[ComputeProviderCapabilities],
    minimum_relative_speedup: float = 0.15,
    minimum_absolute_seconds_saved: float = 5.0,
) -> ComputeRouteDecision:
    """Choose local vs qualified automated cloud from end-to-end estimates.

    Estimates are deliberately supplied by workload-specific measurement code so
    this router cannot confuse raw GPU inference time with total wall time.
    """
    normalized_policy = str(policy or COMPUTE_AUTO).strip().lower()
    if normalized_policy not in {COMPUTE_AUTO, COMPUTE_LOCAL_ONLY, COMPUTE_CLOUD_NO_OVERAGE}:
        raise ValueError(f"unsupported compute policy: {normalized_policy}")
    if not str(workload or "").strip():
        raise ValueError("workload is required")
    if not 0 <= float(minimum_relative_speedup) < 1:
        raise ValueError("minimum_relative_speedup must be in [0, 1)")
    if float(minimum_absolute_seconds_saved) < 0:
        raise ValueError("minimum_absolute_seconds_saved cannot be negative")

    local_seconds = _safe_seconds(local_estimated_seconds)
    cloud_seconds = _safe_seconds(cloud_estimated_seconds)

    if normalized_policy == COMPUTE_LOCAL_ONLY:
        return ComputeRouteDecision("local", None, "local_only_policy", local_seconds, cloud_seconds)

    # V2 has no owner-facing policy that authorizes paid cloud overage.  Both
    # Auto and the explicit cloud option therefore fail local unless provider-
    # side spend protection has been independently verified.  A future paid
    # policy must be an explicit new contract, never inferred from `auto`.
    require_no_overage = normalized_policy in {COMPUTE_AUTO, COMPUTE_CLOUD_NO_OVERAGE}
    qualified = sorted(
        (cap for cap in provider_capabilities if _qualified(cap, require_no_overage=require_no_overage)),
        key=lambda item: item.provider,
    )
    if not qualified:
        return ComputeRouteDecision("local", None, "no_qualified_cloud_provider", local_seconds, cloud_seconds)

    provider = qualified[0].provider
    if local_seconds is None or cloud_seconds is None:
        return ComputeRouteDecision("local", None, "insufficient_end_to_end_estimate", local_seconds, cloud_seconds)
    if local_seconds <= 0:
        return ComputeRouteDecision("local", None, "local_already_negligible", local_seconds, cloud_seconds)

    seconds_saved = local_seconds - cloud_seconds
    relative_speedup = seconds_saved / local_seconds
    materially_faster = (
        cloud_seconds < local_seconds
        and seconds_saved >= float(minimum_absolute_seconds_saved)
        and relative_speedup >= float(minimum_relative_speedup)
    )
    if not materially_faster:
        return ComputeRouteDecision("local", None, "cloud_not_materially_faster_e2e", local_seconds, cloud_seconds)

    return ComputeRouteDecision("cloud", provider, "qualified_cloud_materially_faster_e2e", local_seconds, cloud_seconds)
