#!/usr/bin/env python3
"""Project baseline and managed-CI helpers kept outside the lifecycle CLI."""
from __future__ import annotations
import json, shutil
from pathlib import Path


def detect_technical_baseline(root: Path) -> dict[str, str]:
    languages=[]; framework="NONE_DETECTED"; database="NONE_DETECTED"; package_manager="NONE_DETECTED"
    entry_point="AUTO_DETECT_ON_FIRST_TASK"; run_command="PROJECT_SPECIFIC"; test_command="PROJECT_SPECIFIC"; lint_command="PROJECT_SPECIFIC"; typecheck_command="PROJECT_SPECIFIC"
    build_command="NONE_REQUIRED_OR_PROJECT_SPECIFIC"; install_command="PROJECT_SPECIFIC"; quality_command="PROJECT_SPECIFIC"; quality_capabilities="UNSET"
    package=root/"package.json"
    if package.is_file():
        languages.append("Node/TypeScript/JavaScript")
        try:
            data=json.loads(package.read_text(encoding="utf-8")); deps={**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}; scripts=data.get("scripts") or {}
        except json.JSONDecodeError: deps={}; scripts={}
        for dep,name in [("next","Next.js"),("react","React"),("vue","Vue"),("svelte","Svelte"),("express","Express"),("@nestjs/core","NestJS")]:
            if dep in deps: framework=name; break
        for dep,name in [("prisma","Prisma/SQL"),("@prisma/client","Prisma/SQL"),("mongoose","MongoDB"),("pg","PostgreSQL"),("better-sqlite3","SQLite")]:
            if dep in deps: database=name; break
        if (root/"pnpm-lock.yaml").is_file(): package_manager="pnpm"; install_command="pnpm install --frozen-lockfile"
        elif (root/"yarn.lock").is_file(): package_manager="yarn"; install_command="yarn install --frozen-lockfile"
        elif (root/"package-lock.json").is_file(): package_manager="npm"; install_command="npm ci"
        else: package_manager="npm"; install_command="npm install"
        if "dev" in scripts: run_command=f"{package_manager} run dev"
        elif "start" in scripts: run_command=f"{package_manager} run start"
        if "test" in scripts: test_command=f"{package_manager} run test"
        if "build" in scripts: build_command=f"{package_manager} run build"
        caps=[]
        if "lint" in scripts: lint_command=f"{package_manager} run lint"; caps.append("lint")
        if "typecheck" in scripts: typecheck_command=f"{package_manager} run typecheck"; caps.append("typecheck")
        elif "check" in scripts: typecheck_command=f"{package_manager} run check"; caps.append("typecheck")
        if "test" in scripts: caps.append("test")
        if "build" in scripts: caps.append("build")
        quality_parts=[x for x in (lint_command if lint_command!="PROJECT_SPECIFIC" else "", typecheck_command if typecheck_command!="PROJECT_SPECIFIC" else "", test_command if test_command!="PROJECT_SPECIFIC" else "", build_command if build_command!="NONE_REQUIRED_OR_PROJECT_SPECIFIC" else "") if x]
        if quality_parts: quality_command=" && ".join(quality_parts); quality_capabilities=",".join(dict.fromkeys(caps))
    pyproject=root/"pyproject.toml"
    if pyproject.is_file() or (root/"requirements.txt").is_file() or (root/"setup.py").is_file():
        languages.append("Python"); text=pyproject.read_text(encoding="utf-8",errors="ignore").casefold() if pyproject.is_file() else ""
        for needle,name in [("fastapi","FastAPI"),("django","Django"),("flask","Flask")]:
            if needle in text: framework=name; break
        for needle,name in [("sqlalchemy","SQLAlchemy/SQL"),("psycopg","PostgreSQL"),("sqlite","SQLite")]:
            if needle in text: database=name; break
        if (root/"uv.lock").is_file(): package_manager="uv"; install_command="uv sync --all-extras --dev"
        elif "poetry" in text: package_manager="poetry"; install_command="poetry install --with dev"
        elif (root/"requirements.txt").is_file(): package_manager="pip"; install_command="python -m pip install -r requirements.txt"
        else: package_manager="pip"; install_command="python -m pip install -e ."
        py_quality=[]; caps=[]
        if 'ruff' in text: lint_command='python -m ruff check .'; py_quality.append(lint_command); caps.append('lint')
        if 'mypy' in text or 'pyright' in text:
            typecheck_command='python -m mypy .' if 'mypy' in text else 'python -m pyright'; py_quality.append(typecheck_command); caps.append('typecheck')
        if (root/"tests").exists():
            test_command="python -m pytest -q"; py_quality.append(test_command); caps.append('test')
        if py_quality: quality_command=' && '.join(py_quality); quality_capabilities=','.join(caps)
        for candidate in ("main.py","app.py","src/main.py"):
            if (root/candidate).is_file(): entry_point=candidate; break
    if (root/"go.mod").is_file():
        languages.append("Go"); package_manager="go modules"; install_command="go mod download"; test_command="go test ./..."; lint_command="go vet ./..."; build_command="go build ./..."; quality_command="go vet ./... && go test ./... && go build ./..."; quality_capabilities="lint,test,build"
    if (root/"Cargo.toml").is_file():
        languages.append("Rust"); package_manager="cargo"; install_command="cargo fetch"; test_command="cargo test --all-targets"; lint_command="cargo clippy --all-targets -- -D warnings"; build_command="cargo build"; quality_command="cargo clippy --all-targets -- -D warnings && cargo test --all-targets && cargo build"; quality_capabilities="lint,test,build"
    important=[name for name in ("src","app","tests","test","packages","services") if (root/name).exists()]
    return {"Language/runtime":", ".join(languages) if languages else "AUTO_DETECT_ON_FIRST_TASK","Framework":framework,"Database":database,"Package manager":package_manager,"Entry point":entry_point,"Run command":run_command,"Install command":install_command,"Test command":test_command,"Lint command":lint_command,"Typecheck command":typecheck_command,"Build command":build_command,"CI quality command":quality_command,"CI quality capabilities":quality_capabilities,"Important directories":", ".join(important) if important else "AUTO_DETECT_ON_FIRST_TASK"}


def install_ci_workflow(root: Path) -> bool:
    source=root/"templates"/"CI_GITHUB_ACTIONS.yml"; target=root/".github"/"workflows"/"ai-build-os.yml"
    if target.exists(): return False
    target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(source,target); return True
