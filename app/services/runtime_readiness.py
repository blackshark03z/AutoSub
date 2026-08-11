from __future__ import annotations

"""Managed, machine-local dependencies for the normal AutoSubs workflow.

The application owns only this small manifest and the downloaded executables it
places under the user's local application data.  AutoSubs retains ownership of
its own model cache; its public CLI is used to download and validate ``small``.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path
from typing import Any, Callable


AUTOSUBS_VERSION = "3.8.0"
AUTOSUBS_MODEL = "small"
AUTOSUBS_WINDOWS_URL = (
    "https://github.com/tmoroney/auto-subs/releases/download/v3.8.0/"
    "AutoSubs-windows-x86_64.exe"
)
AUTOSUBS_WINDOWS_SHA256 = "d67851d3234a2ee7744be80423e21f76fea2a6981bd03f3cf5f4ed31daf3a3d6"
ARGOS_VERSION = "1.9.6"
ARGOS_MODEL_ID = "translate-zh_en-1_9"
ARGOS_MODEL_PACKAGE_VERSION = "1.9"


class RuntimeReadinessError(RuntimeError):
    """A supported local dependency could not be made ready."""


ProgressCallback = Callable[[str, str], None]


def runtime_root(product_root: Path) -> Path:
    """Return the one managed runtime root without requiring user setup.

    ``TOOL_AUTO_SUB_RUNTIME_ROOT`` is retained as a test/integration override;
    normal users get an isolated per-user machine location.
    """
    configured = os.environ.get("TOOL_AUTO_SUB_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return (Path(local_app_data) / "ToolAutoSub" / "runtime").resolve()
    return (Path(product_root).resolve() / "runtime").resolve()


def managed_autosubs_binary(product_root: Path) -> Path:
    return runtime_root(product_root) / "autosubs" / "autosubs.exe"


def managed_translation_config_path(product_root: Path) -> Path:
    return runtime_root(product_root) / "translation" / "translation_runtime_config.json"


def runtime_readiness(product_root: Path, *, validate_cached: bool = False) -> dict[str, Any]:
    """Return component state using executable/import/inference validation.

    Cached models are only called ready after a previously successful real
    probe.  ``validate_cached`` repeats that probe, which is used before every
    product workflow so stale caches are never trusted by file presence alone.
    """
    root = Path(product_root).resolve()
    binary = _discover_autosubs_binary(root)
    autosubs = _autosubs_runtime_state(binary)
    model = _autosubs_model_state(root, binary, validate_cached=validate_cached)
    translation = _translation_runtime_state(root, validate_cached=validate_cached)
    return {
        "runtime_root": str(runtime_root(root)),
        "status": "ready" if all(item["state"] == "ready" for item in (autosubs, model, translation["runtime"], translation["model"])) else "not_ready",
        "autosubs_runtime": autosubs,
        "autosubs_small_model": model,
        "argos_runtime": translation["runtime"],
        "argos_zh_en_model": translation["model"],
    }


def ensure_product_runtime_ready(product_root: Path, *, progress: ProgressCallback | None = None) -> dict[str, Any]:
    """Prepare and real-validate all dependencies needed by zh -> en runs."""
    root = Path(product_root).resolve()
    _progress(progress, "checking_runtime", "Checking local runtime")
    before = runtime_readiness(root)
    binary = _discover_autosubs_binary(root)
    if before["autosubs_runtime"]["state"] != "ready":
        _progress(progress, "downloading_autosubs", "Downloading AutoSubs 3.8.0")
        binary = _download_autosubs(root)
    _validate_autosubs_binary(binary)

    if before["autosubs_small_model"]["state"] != "ready":
        _progress(progress, "preparing_autosubs_model", "Preparing AutoSubs small model")
    _probe_autosubs_small(binary)
    _write_json(_autosubs_probe_record(root), {"binary_sha256": _sha256_file(binary), "model": AUTOSUBS_MODEL})

    translation_before = before["argos_runtime"]["state"] == "ready" and before["argos_zh_en_model"]["state"] == "ready"
    if not translation_before:
        _progress(progress, "preparing_translation", "Preparing offline Chinese to English translation")
        _prepare_translation(root)
    _validate_translation(root)
    _progress(progress, "runtime_ready", "Local runtime is ready")
    return runtime_readiness(root, validate_cached=True)


def _discover_autosubs_binary(product_root: Path) -> Path:
    managed = managed_autosubs_binary(product_root)
    if managed.is_file():
        return managed
    # Retain a valid pre-existing developer/portable cache without copying a
    # 69 MB executable.  New user installs use the managed root above.
    legacy = Path(product_root) / "addons" / "autosubs" / "autosubs.exe"
    return legacy if legacy.is_file() else managed


def _autosubs_runtime_state(binary: Path) -> dict[str, str]:
    if not binary.is_file():
        return _state("missing", "AutoSubs 3.8.0 executable is not installed")
    try:
        _validate_autosubs_binary(binary)
    except RuntimeReadinessError as exc:
        return _state("invalid", str(exc))
    return _state("ready", "AutoSubs 3.8.0 executable passed --version validation", path=str(binary))


def _autosubs_model_state(product_root: Path, binary: Path, *, validate_cached: bool) -> dict[str, str]:
    if _autosubs_runtime_state(binary)["state"] != "ready":
        return _state("missing", "AutoSubs small model cannot be checked until the executable is ready")
    record = _read_json(_autosubs_probe_record(product_root))
    if not record or record.get("model") != AUTOSUBS_MODEL or record.get("binary_sha256") != _sha256_file(binary):
        return _state("missing", "AutoSubs small model has not yet passed a real local probe")
    if validate_cached:
        try:
            _probe_autosubs_small(binary)
        except RuntimeReadinessError as exc:
            return _state("invalid", str(exc))
    return _state("ready", "AutoSubs small model passed real executable validation")


def _validate_autosubs_binary(binary: Path) -> None:
    try:
        completed = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, encoding="utf-8", timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeReadinessError("AutoSubs could not be started; check disk space and the downloaded runtime.") from exc
    reported = f"{completed.stdout}\n{completed.stderr}".strip().lower()
    if completed.returncode != 0 or f"autosubs {AUTOSUBS_VERSION}" not in reported.splitlines():
        raise RuntimeReadinessError(f"AutoSubs v{AUTOSUBS_VERSION} validation failed; the runtime is invalid.")


def _download_autosubs(product_root: Path) -> Path:
    destination = managed_autosubs_binary(product_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".exe.partial")
    try:
        with urllib.request.urlopen(AUTOSUBS_WINDOWS_URL, timeout=120) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeReadinessError("AutoSubs download failed. Check the network connection and retry.") from exc
    digest = _sha256_file(partial)
    if digest != AUTOSUBS_WINDOWS_SHA256:
        partial.unlink(missing_ok=True)
        raise RuntimeReadinessError("AutoSubs download failed integrity verification; no runtime was installed.")
    partial.replace(destination)
    try:
        _validate_autosubs_binary(destination)
    except RuntimeReadinessError:
        destination.unlink(missing_ok=True)
        raise
    return destination


def _probe_autosubs_small(binary: Path) -> None:
    """Use AutoSubs' supported inference command, which downloads ``small`` if absent."""
    with tempfile.TemporaryDirectory(prefix="autosub_runtime_probe_") as directory:
        probe = Path(directory) / "silence.wav"
        with wave.open(str(probe), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600)
        try:
            completed = subprocess.run(
                [str(binary), str(probe), "--model", AUTOSUBS_MODEL, "--no-gpu", "--format", "json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=900,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeReadinessError("AutoSubs small model preparation timed out or could not start.") from exc
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[:240]
        raise RuntimeReadinessError(f"AutoSubs small model preparation failed. {detail or 'Check network and free disk space.'}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeReadinessError("AutoSubs small model probe returned invalid output.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise RuntimeReadinessError("AutoSubs small model probe did not return a valid transcript payload.")


def _translation_runtime_state(product_root: Path, *, validate_cached: bool) -> dict[str, dict[str, str]]:
    config = _read_json(managed_translation_config_path(product_root))
    if not config:
        return {"runtime": _state("missing", "Offline Argos runtime is not installed"), "model": _state("missing", "Chinese to English model is not installed")}
    python_path = Path(str(config.get("python_path") or ""))
    packages = Path(str(config.get("packages_root") or ""))
    model = packages / ARGOS_MODEL_ID
    if not python_path.is_file():
        return {"runtime": _state("invalid", "Offline Argos Python runtime is missing"), "model": _state("missing", "Chinese to English model cannot be checked until Argos is ready")}
    try:
        _run_argos_import_probe(python_path, packages)
    except RuntimeReadinessError as exc:
        return {"runtime": _state("invalid", str(exc)), "model": _state("missing", "Chinese to English model cannot be checked until Argos is ready")}
    if not _valid_argos_model_metadata(model):
        return {"runtime": _state("ready", "Argos Translate 1.9.6 import validation passed"), "model": _state("missing", "translate-zh_en-1_9 is not installed")}
    if validate_cached:
        try:
            _run_translation_probe(python_path, packages)
        except RuntimeReadinessError as exc:
            return {"runtime": _state("ready", "Argos Translate 1.9.6 import validation passed"), "model": _state("invalid", str(exc))}
    return {"runtime": _state("ready", "Argos Translate 1.9.6 import validation passed"), "model": _state("ready", "translate-zh_en-1_9 passed real translation validation", path=str(model))}


def _prepare_translation(product_root: Path) -> None:
    root = runtime_root(product_root) / "translation"
    python_path = root / "venv" / "Scripts" / "python.exe"
    packages = root / "packages"
    if not python_path.is_file():
        # Prefer the interpreter already running AutoSub when it contains the
        # exact approved package.  This avoids a duplicate Python/Torch tree on
        # constrained machines; otherwise create the managed isolated runtime.
        try:
            _run_argos_import_probe(Path(sys.executable), packages)
            python_path = Path(sys.executable)
        except RuntimeReadinessError:
            try:
                subprocess.run([sys.executable, "-m", "venv", str(root / "venv")], check=True, timeout=180)
                subprocess.run(
                    [str(python_path), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(Path(product_root) / "requirements-translation.lock.txt")],
                    check=True,
                    timeout=1200,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise RuntimeReadinessError("Offline Argos runtime preparation failed. Check network connection, disk space, and retry.") from exc
    packages.mkdir(parents=True, exist_ok=True)
    if not _valid_argos_model_metadata(packages / ARGOS_MODEL_ID):
        bootstrap = """
from pathlib import Path
from time import time
from argostranslate import package
target = Path(r'''%s''')
if target.exists():
    target.rename(target.with_name(target.name + '.invalid-' + str(int(__import__('time').time()))))
available = package.get_available_packages()
candidate = next((item for item in available if item.type == 'translate' and item.from_code == 'zh' and item.to_code == 'en' and item.package_version == '1.9'), None)
if candidate is None:
    raise RuntimeError('approved translate-zh_en-1_9 package is unavailable')
candidate.install()
installed = next((item.package_path for item in package.get_installed_packages() if item.type == 'translate' and item.from_code == 'zh' and item.to_code == 'en' and item.package_version == '1.9'), None)
if installed is None:
    raise RuntimeError('Argos did not install translate-zh_en-1_9')
if installed.resolve() != target.resolve():
    installed.rename(target)
""" % str(packages / ARGOS_MODEL_ID)
        try:
            subprocess.run(
                [str(python_path), "-c", bootstrap],
                check=True,
                timeout=1200,
                env=_argos_environment(packages),
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeReadinessError("Chinese to English translation model preparation failed. Check network connection and retry.") from exc
    _write_json(
        managed_translation_config_path(product_root),
        {"python_path": str(python_path), "packages_root": str(packages), "model_id": ARGOS_MODEL_ID, "timeout_seconds": 120},
    )


def _validate_translation(product_root: Path) -> None:
    config = _read_json(managed_translation_config_path(product_root))
    if not config:
        raise RuntimeReadinessError("Offline translation runtime configuration was not created.")
    python_path = Path(str(config["python_path"]))
    packages = Path(str(config["packages_root"]))
    _run_argos_import_probe(python_path, packages)
    if not _valid_argos_model_metadata(packages / ARGOS_MODEL_ID):
        raise RuntimeReadinessError("Chinese to English translation model metadata validation failed.")
    _run_translation_probe(python_path, packages)


def _run_argos_import_probe(python_path: Path, packages: Path) -> None:
    command = "import importlib.metadata; import argostranslate.translate; assert importlib.metadata.version('argostranslate') == '1.9.6'; print('argos-ready')"
    try:
        completed = subprocess.run([str(python_path), "-c", command], capture_output=True, text=True, encoding="utf-8", timeout=60, check=False, env=_argos_environment(packages))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeReadinessError("Offline Argos runtime could not be started.") from exc
    if completed.returncode != 0 or "argos-ready" not in completed.stdout:
        raise RuntimeReadinessError("Offline Argos 1.9.6 validation failed; the runtime is invalid.")


def _run_translation_probe(python_path: Path, packages: Path) -> None:
    command = "import argostranslate.translate as t; value=t.get_translation_from_codes('zh','en').translate('你好'); assert value.strip(); print('translation-ready')"
    try:
        completed = subprocess.run([str(python_path), "-c", command], capture_output=True, text=True, encoding="utf-8", timeout=120, check=False, env=_argos_environment(packages))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeReadinessError("Chinese to English model validation timed out.") from exc
    if completed.returncode != 0 or "translation-ready" not in completed.stdout:
        raise RuntimeReadinessError("Chinese to English translation model validation failed; retry preparation.")


def _argos_environment(packages: Path) -> dict[str, str]:
    translation_root = packages.parent
    return {**os.environ, "ARGOS_PACKAGES_DIR": str(packages), "XDG_CACHE_HOME": str(translation_root / "cache"), "PYTHONIOENCODING": "utf-8"}


def _valid_argos_model_metadata(model: Path) -> bool:
    try:
        metadata = json.loads((model / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return bool(
        metadata.get("package_version") == ARGOS_MODEL_PACKAGE_VERSION
        and str(metadata.get("argos_version") or "").startswith("1.9")
        and metadata.get("from_code") == "zh"
        and metadata.get("to_code") == "en"
        and (model / "model" / "model.bin").is_file()
    )


def _autosubs_probe_record(product_root: Path) -> Path:
    return runtime_root(product_root) / "autosubs" / "small_probe.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state(state: str, detail: str, *, path: str | None = None) -> dict[str, str]:
    value = {"state": state, "detail": detail}
    if path:
        value["path"] = path
    return value


def _progress(callback: ProgressCallback | None, state: str, message: str) -> None:
    if callback is not None:
        callback(state, message)
