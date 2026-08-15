"""Windows double-click launcher for the repository-local AutoSub UI.

This deliberately starts the same Uvicorn entry point as ``run_app.ps1``.  It
only owns process orchestration: the application itself continues to own
runtime preparation, transcription, translation, preview, and export.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


HOST = "127.0.0.1"
PORT = 8173
UI_URL = f"http://{HOST}:{PORT}/"
HEALTH_URL = f"{UI_URL}api/health"
DEFAULT_READY_TIMEOUT_SECONDS = 60.0
POLL_INTERVAL_SECONDS = 0.25


class LaunchError(RuntimeError):
    """A user-actionable local launch failure."""


@dataclass(frozen=True)
class LaunchResult:
    started_new_server: bool
    log_path: Path


def _is_autosub_health(payload: object) -> bool:
    return isinstance(payload, dict) and payload.get("status") == "ok" and payload.get("bind") == HOST


def autosub_is_ready(urlopen: Callable[..., object] = urllib.request.urlopen) -> bool:
    """Return true only for AutoSub's expected health contract."""
    try:
        with urlopen(HEALTH_URL, timeout=1.0) as response:
            if getattr(response, "status", 200) != 200:
                return False
            return _is_autosub_health(json.loads(response.read().decode("utf-8")))
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def port_is_in_use(host: str = HOST, port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.25)
        return connection.connect_ex((host, port)) == 0


def resolve_project_root(candidate: str | Path | None = None) -> Path:
    root = Path(candidate).resolve() if candidate else Path(__file__).resolve().parents[1]
    required = (root / "app" / "main.py", root / "run_app.ps1", root / "operator" / "run_config.json")
    if not all(path.is_file() for path in required):
        raise LaunchError(f"This launcher must be run from a prepared AutoSub project folder. Checked: {root}")
    return root


def launcher_log_path(root: Path) -> Path:
    log_dir = root / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "autosub-launcher.log"


@contextmanager
def launch_lock(root: Path, timeout_seconds: float = DEFAULT_READY_TIMEOUT_SECONDS) -> Iterator[None]:
    """Serialize concurrent double-clicks while the first server becomes ready."""
    lock_path = root / "runtime" / "autosub-launcher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + timeout_seconds
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LaunchError("Another AutoSub launch is still starting. Please wait a moment and try again.")
                    time.sleep(POLL_INTERVAL_SECONDS)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            # The product target is Windows; this keeps focused tests usable on other hosts.
            yield


def start_server(root: Path, log_path: Path) -> subprocess.Popen[bytes]:
    environment = os.environ.copy()
    environment["TOOL_AUTO_SUB_ROOT"] = str(root)
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with log_path.open("ab") as log_file:
        return subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(PORT)],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )


def wait_for_readiness(process: subprocess.Popen[bytes], log_path: Path, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if autosub_is_ready():
            return
        exit_code = process.poll()
        if exit_code is not None:
            raise LaunchError(f"AutoSub stopped during startup (exit code {exit_code}). See log: {log_path}")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise LaunchError(f"AutoSub did not become ready within {timeout_seconds:g} seconds. See log: {log_path}")


def launch(
    root: Path,
    *,
    timeout_seconds: float = DEFAULT_READY_TIMEOUT_SECONDS,
    open_browser: Callable[..., bool] = webbrowser.open,
) -> LaunchResult:
    """Open a verified existing instance or start exactly one normal server."""
    log_path = launcher_log_path(root)
    if autosub_is_ready():
        open_browser(UI_URL, new=2)
        return LaunchResult(started_new_server=False, log_path=log_path)

    with launch_lock(root, timeout_seconds):
        # A concurrent double-click may have completed while this launcher waited.
        if autosub_is_ready():
            open_browser(UI_URL, new=2)
            return LaunchResult(started_new_server=False, log_path=log_path)
        if port_is_in_use():
            raise LaunchError(
                f"Port {PORT} is already being used by another application. AutoSub did not close it. "
                f"Close the other application, then double-click Run AutoSub again."
            )

        process = start_server(root, log_path)
        wait_for_readiness(process, log_path, timeout_seconds)
        open_browser(UI_URL, new=2)
        return LaunchResult(started_new_server=True, log_path=log_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the local AutoSub Simple UI.")
    parser.add_argument("--project-root", help="AutoSub project root; normally derived from this launcher.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_READY_TIMEOUT_SECONDS, help="Readiness timeout in seconds.")
    parser.add_argument("--no-browser", action="store_true", help="For diagnostics only: do not open the UI browser.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.timeout <= 0:
            raise LaunchError("Readiness timeout must be greater than zero.")
        root = resolve_project_root(args.project_root)
        result = launch(root, timeout_seconds=args.timeout, open_browser=(lambda *_args, **_kwargs: True) if args.no_browser else webbrowser.open)
        action = "started" if result.started_new_server else "reused the existing"
        print(f"AutoSub {action} local server and opened the Simple UI.")
        return 0
    except LaunchError as exc:
        print(f"AutoSub could not start: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # Keep the double-click flow actionable rather than exposing a raw traceback.
        print(f"AutoSub could not start because of an unexpected local error ({type(exc).__name__}). See runtime/logs/autosub-launcher.log.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
