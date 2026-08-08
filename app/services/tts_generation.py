import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.provider_cache import build_request_hash
from app.db.session import session_scope
from app.domain.models import TTSGeneration, TTSRequestReservation
from app.providers.tts.base import TTSProvider, TTSRateLimitError, TTSRequest, TTSUncertainError
from app.providers.tts.fake import tts_request_payload
from app.services.timeline import load_latest_timeline
from app.services.tts_units import attach_tts_synthesis_units


def resolve_voice_id(configured_voice_id: str | None, provider: TTSProvider) -> str:
    if configured_voice_id:
        return configured_voice_id
    voices = provider.list_voices()
    if not voices:
        raise ValueError("No TTS voices available")
    return voices[0].get("voice_id") or voices[0].get("voiceId") or voices[0]["voice_id"]


def generate_tts_for_segment(
    project_id: str,
    segment: dict,
    provider: TTSProvider,
    voice_id: str,
    previous_request_ids: list[str] | None = None,
    previous_text: str | None = None,
    next_text: str | None = None,
    provider_request_version: str | None = None,
    **single_flight_options,
) -> dict:
    return generate_tts_for_unit(
        project_id,
        segment,
        provider,
        voice_id,
        previous_request_ids=previous_request_ids,
        previous_text=previous_text,
        next_text=next_text,
        provider_request_version=provider_request_version,
        **single_flight_options,
    )


def generate_tts_for_unit(
    project_id: str,
    unit: dict,
    provider: TTSProvider,
    voice_id: str,
    previous_request_ids: list[str] | None = None,
    previous_text: str | None = None,
    next_text: str | None = None,
    provider_request_version: str | None = None,
    *,
    lease_seconds: float = 300.0,
    wait_timeout_seconds: float = 310.0,
    poll_seconds: float = 0.05,
) -> dict:
    text = unit.get("spoken_text") or unit.get("subtitle_text") or unit.get("translated_text")
    if not text:
        raise ValueError("TTS synthesis unit has no spoken text")
    if len(text) > 700:
        raise ValueError("TTS synthesis unit text exceeds TTS guardrail")
    if previous_text and previous_request_ids:
        raise ValueError("TTS request cannot combine previous_text with previous_request_ids")

    request = TTSRequest(
        project_id=project_id,
        segment_id=unit["id"],
        text=text,
        voice_id=voice_id,
        model=provider.model,
        previous_request_ids=(previous_request_ids or [])[-3:],
        previous_text=previous_text,
        next_text=next_text,
        output_format=getattr(provider, "output_format", "mp3_44100_128"),
        provider_request_version=provider_request_version or getattr(provider, "provider_request_version", "tts-v2"),
    )
    request_hash = build_request_hash(tts_request_payload(provider.provider_name, request))
    existing = find_ready_generation_by_hash(request_hash)
    if existing is not None:
        unit["active_tts_generation_id"] = existing["generation_id"]
        return existing

    owner_token = uuid4().hex
    action = _acquire_or_wait_for_reservation(
        request_hash=request_hash,
        provider=provider,
        voice_id=voice_id,
        owner_token=owner_token,
        lease_seconds=lease_seconds,
        wait_timeout_seconds=wait_timeout_seconds,
        poll_seconds=poll_seconds,
    )
    if action == "ready":
        ready = find_ready_generation_by_hash(request_hash)
        if ready is None:
            _mark_reservation_uncertain(request_hash, "ready_artifact_missing_or_corrupt")
            return _uncertain_result(request_hash, "ready_artifact_missing_or_corrupt", len(text))
        unit["active_tts_generation_id"] = ready["generation_id"]
        return ready
    if action == "uncertain":
        return _uncertain_result(request_hash, "single_flight_uncertain", len(text))

    generation_id = f"tts_{uuid4().hex[:12]}"
    settings = get_settings()
    output_dir = settings.data_dir / "projects" / project_id / "tts" / "units"
    output_path = output_dir / f"{unit['id']}_{generation_id}.wav"
    _mark_reservation_running(request_hash, owner_token, lease_seconds)
    try:
        result = provider.synthesize(request, output_path)
    except TTSRateLimitError:
        _mark_reservation_failed(request_hash, owner_token, "rate_limited")
        raise
    except TTSUncertainError as exc:
        return record_uncertain_generation(
            project_id=project_id,
            segment_id=unit["id"],
            generation_id=generation_id,
            provider=provider,
            voice_id=voice_id,
            request_hash=request_hash,
            character_count=len(text),
            reason=type(exc).__name__,
            owner_token=owner_token,
        )
    except Exception:
        _mark_reservation_failed(request_hash, owner_token, "provider_error_before_ready")
        raise

    if result.request_hash != request_hash:
        _mark_reservation_uncertain(request_hash, "provider_request_hash_mismatch")
        raise TTSUncertainError("Provider returned a mismatched canonical request hash")
    digest = sha256_file(result.audio_path)
    status = "uncertain" if result.uncertain else "ready"
    with session_scope() as session:
        session.add(
            TTSGeneration(
                project_id=project_id,
                segment_id=unit["id"],
                generation_id=generation_id,
                provider=result.provider,
                model=result.model,
                voice_id=result.voice_id,
                request_hash=result.request_hash,
                cache_status=result.cache_status,
                status=status,
                artifact_path=str(result.audio_path.resolve()),
                sha256=digest,
                character_count=result.character_count,
                request_id=result.request_id,
                credential_ref=result.credential_ref,
            )
        )
        reservation = _owned_reservation(session, request_hash, owner_token)
        reservation.status = status
        reservation.generation_id = generation_id
        reservation.owner_token = None
        reservation.lease_expires_at = None
        reservation.updated_at = _utcnow()
    unit["active_tts_generation_id"] = generation_id
    return {
        "generation_id": generation_id,
        "request_hash": result.request_hash,
        "cache_status": result.cache_status,
        "request_id": result.request_id,
        "artifact_path": str(result.audio_path.resolve()),
        "sha256": digest,
        "character_count": result.character_count,
        "status": status,
        "tts_unit_id": unit["id"],
        "segment_ids": list(unit.get("segment_ids", [unit["id"]])),
    }


def generate_tts_for_timeline(project_id: str, timeline: dict, provider: TTSProvider, voice_id: str) -> list[dict]:
    units = attach_tts_synthesis_units(timeline)
    generations: list[dict] = []
    previous_request_ids: list[str] = []
    for index, unit in enumerate(units):
        next_text = units[index + 1]["spoken_text"] if index + 1 < len(units) else None
        result = generate_tts_for_unit(
            project_id,
            unit,
            provider,
            voice_id,
            previous_request_ids=previous_request_ids,
            next_text=next_text,
        )
        if result.get("request_id"):
            previous_request_ids.append(result["request_id"])
        generations.append(result)
    return generations


def find_ready_generation_by_hash(request_hash: str) -> dict | None:
    with session_scope() as session:
        generation = (
            session.query(TTSGeneration)
            .filter(TTSGeneration.request_hash == request_hash)
            .filter(TTSGeneration.status == "ready")
            .order_by(TTSGeneration.created_at.desc(), TTSGeneration.id.desc())
            .first()
        )
        if generation is None:
            return None
        path = Path(generation.artifact_path)
        if not path.exists() or sha256_file(path) != generation.sha256:
            return None
        return {
            "generation_id": generation.generation_id,
            "request_hash": generation.request_hash,
            "cache_status": "hit",
            "request_id": generation.request_id,
            "artifact_path": generation.artifact_path,
            "sha256": generation.sha256,
            "character_count": generation.character_count,
            "status": generation.status,
            "tts_unit_id": generation.segment_id,
        }


def record_uncertain_generation(
    project_id: str,
    segment_id: str,
    generation_id: str,
    provider: TTSProvider,
    voice_id: str,
    request_hash: str,
    character_count: int,
    reason: str,
    owner_token: str | None = None,
) -> dict:
    with session_scope() as session:
        session.add(
            TTSGeneration(
                project_id=project_id,
                segment_id=segment_id,
                generation_id=generation_id,
                provider=provider.provider_name,
                model=provider.model,
                voice_id=voice_id,
                request_hash=request_hash,
                cache_status="uncertain",
                status="uncertain",
                artifact_path="",
                sha256="",
                character_count=character_count,
                request_id=reason,
                credential_ref=None,
            )
        )
        reservation = session.query(TTSRequestReservation).filter_by(request_hash=request_hash).one_or_none()
        if reservation is not None and (owner_token is None or reservation.owner_token == owner_token):
            reservation.status = "uncertain"
            reservation.generation_id = generation_id
            reservation.owner_token = None
            reservation.lease_expires_at = None
            reservation.last_error = reason
            reservation.updated_at = _utcnow()
    result = _uncertain_result(request_hash, reason, character_count)
    result["generation_id"] = generation_id
    return result


def generate_fake_tts_for_latest_timeline(project_id: str, provider: TTSProvider, limit: int = 3) -> dict:
    timeline = load_latest_timeline(project_id)
    voice_id = resolve_voice_id(None, provider)
    units = attach_tts_synthesis_units(timeline)[:limit]
    generations = [generate_tts_for_unit(project_id, unit, provider, voice_id) for unit in units]
    return {"project_id": project_id, "generations": generations}


def _acquire_or_wait_for_reservation(
    *,
    request_hash: str,
    provider: TTSProvider,
    voice_id: str,
    owner_token: str,
    lease_seconds: float,
    wait_timeout_seconds: float,
    poll_seconds: float,
) -> str:
    deadline = time.monotonic() + wait_timeout_seconds
    try:
        with session_scope() as session:
            now = _utcnow()
            session.add(
                TTSRequestReservation(
                    request_hash=request_hash,
                    provider=provider.provider_name,
                    model=provider.model,
                    voice_id=voice_id,
                    status="reserved",
                    owner_token=owner_token,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    created_at=now,
                    updated_at=now,
                )
            )
            session.flush()
        return "owner"
    except IntegrityError:
        pass

    while time.monotonic() < deadline:
        with session_scope() as session:
            reservation = session.query(TTSRequestReservation).filter_by(request_hash=request_hash).one()
            now = _utcnow()
            if reservation.status == "ready":
                return "ready"
            if reservation.status == "uncertain":
                return "uncertain"
            if reservation.status == "running" and _lease_expired(reservation.lease_expires_at, now):
                reservation.status = "uncertain"
                reservation.owner_token = None
                reservation.lease_expires_at = None
                reservation.last_error = "running_lease_expired_post_charge_unknown"
                reservation.updated_at = now
                return "uncertain"
            if reservation.status in {"reserved", "failed"} and (
                reservation.status == "failed" or _lease_expired(reservation.lease_expires_at, now)
            ):
                reservation.status = "reserved"
                reservation.owner_token = owner_token
                reservation.lease_expires_at = now + timedelta(seconds=lease_seconds)
                reservation.last_error = None
                reservation.updated_at = now
                return "owner"
        time.sleep(poll_seconds)
    raise TimeoutError("Timed out waiting for the TTS single-flight owner")


def _mark_reservation_running(request_hash: str, owner_token: str, lease_seconds: float) -> None:
    with session_scope() as session:
        reservation = _owned_reservation(session, request_hash, owner_token)
        if reservation.status != "reserved":
            raise RuntimeError("TTS reservation is not in reserved state")
        now = _utcnow()
        reservation.status = "running"
        reservation.lease_expires_at = now + timedelta(seconds=lease_seconds)
        reservation.updated_at = now


def _mark_reservation_failed(request_hash: str, owner_token: str, reason: str) -> None:
    with session_scope() as session:
        reservation = _owned_reservation(session, request_hash, owner_token)
        reservation.status = "failed"
        reservation.owner_token = None
        reservation.lease_expires_at = None
        reservation.last_error = reason
        reservation.updated_at = _utcnow()


def _mark_reservation_uncertain(request_hash: str, reason: str) -> None:
    with session_scope() as session:
        reservation = session.query(TTSRequestReservation).filter_by(request_hash=request_hash).one_or_none()
        if reservation is not None:
            reservation.status = "uncertain"
            reservation.owner_token = None
            reservation.lease_expires_at = None
            reservation.last_error = reason
            reservation.updated_at = _utcnow()


def _owned_reservation(session, request_hash: str, owner_token: str) -> TTSRequestReservation:
    reservation = session.query(TTSRequestReservation).filter_by(request_hash=request_hash).one()
    if reservation.owner_token != owner_token:
        raise RuntimeError("TTS reservation ownership was lost")
    return reservation


def _uncertain_result(request_hash: str, reason: str, character_count: int) -> dict:
    return {
        "generation_id": None,
        "request_hash": request_hash,
        "cache_status": "uncertain",
        "request_id": reason,
        "artifact_path": "",
        "sha256": "",
        "character_count": character_count,
        "status": "uncertain",
    }


def _lease_expired(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return True
    if value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    return value <= now


def _utcnow() -> datetime:
    return datetime.utcnow()
