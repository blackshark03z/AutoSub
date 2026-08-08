#!/usr/bin/env python3
"""Detect and run conservative product checks for common vibe-code stacks."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def detect(root: Path) -> list[tuple[str, list[str]]]:
    checks: list[tuple[str, list[str]]] = []

    package = root / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {}) or {}
        except json.JSONDecodeError:
            scripts = {}
        runner = "npm"
        if (root / "pnpm-lock.yaml").is_file() and shutil.which("pnpm"):
            runner = "pnpm"
        elif (root / "yarn.lock").is_file() and shutil.which("yarn"):
            runner = "yarn"
        for name in ["lint", "typecheck", "check", "test", "build"]:
            if name not in scripts:
                continue
            if runner == "npm":
                argv = ["npm", "run", name]
            elif runner == "pnpm":
                argv = ["pnpm", "run", name]
            else:
                argv = ["yarn", name]
            checks.append((f"node:{name}", argv))

    python_markers = any((root / name).is_file() for name in ["pyproject.toml", "setup.cfg", "setup.py", "requirements.txt"])
    if python_markers:
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8", errors="ignore") if (root / "pyproject.toml").is_file() else ""
        if (root / "tests").exists() and importlib.util.find_spec("pytest") is not None:
            checks.append(("python:pytest", [sys.executable, "-m", "pytest", "-q"]))
        if "ruff" in pyproject.casefold() and importlib.util.find_spec("ruff") is not None:
            checks.append(("python:ruff", [sys.executable, "-m", "ruff", "check", "."]))
        if "mypy" in pyproject.casefold() and importlib.util.find_spec("mypy") is not None:
            checks.append(("python:mypy", [sys.executable, "-m", "mypy", "."]))

    if (root / "go.mod").is_file() and shutil.which("go"):
        checks.append(("go:test", ["go", "test", "./..."]))
    if (root / "Cargo.toml").is_file() and shutil.which("cargo"):
        checks.append(("rust:test", ["cargo", "test", "--all-targets"]))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run detected product CI checks")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--ci", action="store_true", help="Run detected checks; fail on the first product failure")
    args = parser.parse_args()
    root = args.root.resolve()
    checks = detect(root)
    if not checks:
        print("PROJECT_CI: no standard product checks detected; add project-specific CI commands if needed")
        return 0
    for name, argv in checks:
        print(f"{name}: {' '.join(argv)}")
    if args.list and not args.ci:
        return 0
    if not args.ci:
        return 0
    for name, argv in checks:
        print(f"\n== {name} ==", flush=True)
        result = subprocess.run(argv, cwd=root)
        if result.returncode:
            print(f"PROJECT_CI: FAIL {name} exit={result.returncode}")
            return result.returncode
    print(f"PROJECT_CI: PASS checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
