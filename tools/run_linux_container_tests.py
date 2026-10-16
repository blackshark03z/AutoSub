#!/usr/bin/env python3
"""
AutoSub Cloud V2 — Container Test Runner & Environment Verifier (WP-DL-001B)
Executes tests in Docker if available, or verifies POSIX/Linux CPU portability invariants.
"""

import os
import shutil
import subprocess
import sys


def main() -> int:
    docker_bin = shutil.which("docker")
    if docker_bin:
        print("[INFO] Docker runtime detected. Building and executing inside Linux CPU container...")
        build_cmd = ["docker", "build", "-t", "autosub-torrent-discovery:cpu", "-f", "Dockerfile.cpu", "."]
        subprocess.run(build_cmd, check=True)
        run_cmd = ["docker", "run", "--rm", "autosub-torrent-discovery:cpu"]
        return subprocess.run(run_cmd).returncode
    else:
        print("[WARN] Local host does not have 'docker' in PATH (Windows non-container host).")
        print("[INFO] Verifying tests under Python headless execution...")
        pytest_cmd = [sys.executable, "-m", "pytest", "-v", "tests/test_torrent_source_discovery.py"]
        return subprocess.run(pytest_cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
