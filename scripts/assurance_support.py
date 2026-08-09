#!/usr/bin/env python3
"""Assurance levels and Guardian-backed review trust for Senior AI Build OS v1.16.

A0 advisory only; A1 repo-local kernel; A2 external Guardian/reviewer signature; A3 protected
merge attested by trusted CI; A4 isolated worker/reviewer authority attested by runtime.
A2 proves reviewer identity/attestation, not write isolation.
The module deliberately distinguishes achieved assurance from desired policy.
"""
from __future__ import annotations
import hashlib, json, os, subprocess, tempfile
from pathlib import Path
from typing import Any

CONFIG = Path('config/assurance.json')
LEVELS = {'A0':0,'A1':1,'A2':2,'A3':3,'A4':4}


def load_config(root: Path) -> dict[str, Any]:
    path=root/CONFIG
    if not path.is_file():
        return {'schema_version':1,'review_trust':{'R2_when_review_triggered':'EXTERNAL_RUNTIME','R3':'EXTERNAL_RUNTIME'},'guardian':{}}
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except json.JSONDecodeError:
        return {}


def public_key_path(root: Path) -> Path | None:
    cfg=load_config(root); env_name=str((cfg.get('guardian') or {}).get('public_key_env') or 'AI_BUILD_OS_GUARDIAN_PUBLIC_KEY')
    raw=os.environ.get(env_name,'').strip()
    if not raw:return None
    p=Path(raw).expanduser().resolve()
    return p if p.is_file() else None


def review_requirement(root: Path, risk: str, *, r2_review_triggered: bool=False) -> str:
    trust=load_config(root).get('review_trust') or {}
    if risk=='R3': return str(trust.get('R3') or 'SIGNED_GUARDIAN').upper()
    if risk=='R2' and r2_review_triggered:return str(trust.get('R2_when_review_triggered') or 'SIGNED_GUARDIAN').upper()
    return str(trust.get(risk) or 'NONE').upper()


def _canonical_attestation_bytes(value: dict[str, Any]) -> bytes:
    payload={k:v for k,v in value.items() if k not in {'signature_b64','signature_alg','key_fingerprint_sha256'}}
    return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def key_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_guardian_signature(root: Path, value: dict[str, Any]) -> list[str]:
    errors=[]
    signature=str(value.get('signature_b64') or '').strip()
    alg=str(value.get('signature_alg') or '').strip().lower()
    claimed_fp=str(value.get('key_fingerprint_sha256') or '').strip()
    if not signature: return ['Guardian attestation missing signature_b64']
    if alg!='ed25519-openssl': return [f'Unsupported Guardian signature algorithm: {alg or "missing"}']
    pub=public_key_path(root)
    if pub is None:return ['SIGNED_GUARDIAN review requires external public key path via AI_BUILD_OS_GUARDIAN_PUBLIC_KEY (or configured env name)']
    actual_fp=key_fingerprint(pub)
    if claimed_fp and claimed_fp!=actual_fp:errors.append('Guardian public-key fingerprint mismatch')
    try:
        import base64
        sig=base64.b64decode(signature,validate=True)
    except Exception:
        return errors+['Guardian signature_b64 is invalid base64']
    with tempfile.TemporaryDirectory(prefix='ai-build-os-verify-') as td:
        data=Path(td)/'payload.json'; sigp=Path(td)/'sig.bin'
        data.write_bytes(_canonical_attestation_bytes(value)); sigp.write_bytes(sig)
        try:
            proc=subprocess.run(['openssl','pkeyutl','-verify','-pubin','-inkey',str(pub),'-rawin','-in',str(data),'-sigfile',str(sigp)],capture_output=True,text=True,timeout=10,check=False)
        except FileNotFoundError:
            try:
                from cryptography.hazmat.primitives import serialization
                from cryptography.exceptions import InvalidSignature
                key = serialization.load_pem_public_key(pub.read_bytes())
                key.verify(sig, data.read_bytes())
            except ImportError as exc:
                return errors+[f'Guardian signature verification unavailable: {exc}']
            except InvalidSignature:
                errors.append('Guardian signature verification failed')
            except Exception as exc:
                return errors+[f'Guardian signature verification unavailable: {exc}']
        except subprocess.TimeoutExpired as exc:
            return errors+[f'Guardian signature verification unavailable: {exc}']
        else:
            if proc.returncode!=0:errors.append('Guardian signature verification failed')
    return errors


def achieved_assurance(root: Path) -> dict[str, Any]:
    cfg=load_config(root); guardian=cfg.get('guardian') or {}
    level='A1' if (root/'scripts/ai_os.py').is_file() and (root/'.ai').exists() else 'A0'
    reasons=['repo-local lifecycle/validator present'] if level=='A1' else ['repo kernel not initialized']
    pub=public_key_path(root)
    external_env=str(guardian.get('external_guardian_attested_env') or 'AI_BUILD_OS_GUARDIAN_EXTERNAL_ATTESTED')
    external_attested=os.environ.get(external_env,'').strip().casefold() in {'1','true','yes','attested'}
    if pub is not None and external_attested:
        level='A2'; reasons.append('external Guardian/reviewer authority is attested and its signature can be verified (no worker write isolation implied)')
    elif pub is not None:
        reasons.append('Guardian public key is configured, but external Guardian authority is not attested; assurance remains A1')
    protected_env=str(guardian.get('protected_merge_env') or 'AI_BUILD_OS_PROTECTED_MERGE_ATTESTED')
    if LEVELS[level]>=2 and os.environ.get(protected_env,'').strip().casefold() in {'1','true','yes','attested'}:
        level='A3'; reasons.append('trusted runtime attests protected merge/required CI')
    isolated_env=str(guardian.get('isolated_worker_env') or 'AI_BUILD_OS_ISOLATED_WORKER_ATTESTED')
    if LEVELS[level]>=3 and os.environ.get(isolated_env,'').strip().casefold() in {'1','true','yes','attested'}:
        level='A4'; reasons.append('trusted runtime attests isolated worker/reviewer authority')
    return {'level':level,'reasons':reasons,'public_key':str(pub) if pub else None,'attestation_basis':'TRUSTED_RUNTIME_ENV_CLAIM' if level in {'A2','A3','A4'} else 'LOCAL_KERNEL_OBSERVATION','warning':('A2-A4 environment attestations are claims supplied by the outer trusted runtime; the repo kernel does not independently prove process/merge isolation.' if level in {'A2','A3','A4'} else 'Repository-local controls do not stop an actor with unrestricted repository write authority.')}
