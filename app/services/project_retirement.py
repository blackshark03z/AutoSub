from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.paths import ensure_within_root
from app.domain.models import (
    Artifact,
    ContentRevision,
    Job,
    MediaAsset,
    Project,
    SimpleWorkflowRun,
    SubtitleTrack,
    SubtitleTrackItem,
    TTSGeneration,
    TimelineRevision,
)


CANONICAL_PROJECT_IDS = {"vertical_slice_cp07"}
RETIREMENT_LEDGER = "project_retirements.json"


@dataclass
class ProjectRetirementResult:
    project_id: str
    status: str
    dry_run: bool
    project_path: str
    db_rows: dict[str, int] = field(default_factory=dict)
    filesystem_removed: bool = False
    bytes_removed: int = 0
    files_removed: int = 0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "project_path": self.project_path,
            "db_rows": self.db_rows,
            "filesystem_removed": self.filesystem_removed,
            "bytes_removed": self.bytes_removed,
            "files_removed": self.files_removed,
            "message": self.message,
        }


class ProjectRetirementError(RuntimeError):
    pass


class ProjectRetirementService:
    def __init__(self, *, root: Path | None = None, projects_root: Path | None = None, ledger_path: Path | None = None) -> None:
        settings = get_settings()
        self.root = (root or settings.root).resolve()
        self.projects_root = (projects_root or settings.data_dir / "projects").resolve()
        self.ledger_path = (ledger_path or settings.data_dir / RETIREMENT_LEDGER).resolve()

    def retire_project(self, session: Session, project_id: str, *, dry_run: bool = True) -> ProjectRetirementResult:
        if project_id in CANONICAL_PROJECT_IDS:
            raise ProjectRetirementError(f"Refusing to retire canonical project: {project_id}")
        self._validate_project_id(project_id)
        project_path = self._project_path(project_id)
        ledger = self._read_ledger()
        exists_in_db = self._exists_in_db(session, project_id)
        exists_on_disk = project_path.exists()
        if not exists_in_db and not exists_on_disk:
            if project_id in ledger:
                return ProjectRetirementResult(project_id, "already_retired", dry_run, str(project_path), message="Project was previously retired.")
            raise ProjectRetirementError(f"Unknown project id: {project_id}")
        stats = self._validate_filesystem(project_path, require_exists=exists_on_disk)
        row_counts = self._row_counts(session, project_id)
        result = ProjectRetirementResult(
            project_id=project_id,
            status="dry_run" if dry_run else "retired",
            dry_run=dry_run,
            project_path=str(project_path),
            db_rows=row_counts,
            bytes_removed=stats["bytes"],
            files_removed=stats["files"],
            message="Project retirement validated." if dry_run else "Project retired.",
        )
        if dry_run:
            return result
        self._delete_db_rows(session, project_id)
        session.flush()
        fk_violations = session.execute(text("PRAGMA foreign_key_check")).all()
        if fk_violations:
            raise ProjectRetirementError(f"Foreign key violations after retirement: {fk_violations}")
        session.commit()
        if project_path.exists():
            shutil.rmtree(project_path)
            result.filesystem_removed = True
        self._record_ledger(project_id, result)
        return result

    def _validate_project_id(self, project_id: str) -> None:
        if not project_id or any(part in project_id for part in ("..", "/", "\\")):
            raise ProjectRetirementError("Project id must be an exact directory name.")

    def _project_path(self, project_id: str) -> Path:
        path = ensure_within_root(self.projects_root, self.projects_root / project_id)
        if path.parent != self.projects_root:
            raise ProjectRetirementError(f"Project path is not a direct project directory: {path}")
        return path

    def _validate_filesystem(self, project_path: Path, *, require_exists: bool) -> dict[str, int]:
        if project_path == self.projects_root / "vertical_slice_cp07":
            raise ProjectRetirementError("Refusing to retire canonical project path.")
        if project_path.exists() and project_path.is_symlink():
            raise ProjectRetirementError(f"Refusing symlink or junction candidate: {project_path}")
        if not project_path.exists():
            if require_exists:
                raise ProjectRetirementError(f"Project path does not exist: {project_path}")
            return {"bytes": 0, "files": 0}
        bytes_total = 0
        files_total = 0
        for base, dirnames, filenames in os.walk(project_path, topdown=True, followlinks=False):
            base_path = Path(base)
            dirnames[:] = [name for name in dirnames if not (base_path / name).is_symlink()]
            for filename in filenames:
                file_path = base_path / filename
                if file_path.is_symlink():
                    raise ProjectRetirementError(f"Refusing symlink file inside project: {file_path}")
                stat = file_path.stat()
                bytes_total += stat.st_size
                files_total += 1
        return {"bytes": bytes_total, "files": files_total}

    def _exists_in_db(self, session: Session, project_id: str) -> bool:
        return session.execute(select(Project.id).where(Project.project_id == project_id)).first() is not None

    def _row_counts(self, session: Session, project_id: str) -> dict[str, int]:
        tracks = [row[0] for row in session.execute(select(SubtitleTrack.track_id).where(SubtitleTrack.project_id == project_id)).all()]
        return {
            "subtitle_track_items": self._count(session, SubtitleTrackItem, SubtitleTrackItem.track_id.in_(tracks)) if tracks else 0,
            "subtitle_tracks": self._count(session, SubtitleTrack, SubtitleTrack.project_id == project_id),
            "simple_workflow_runs": self._count(session, SimpleWorkflowRun, SimpleWorkflowRun.project_id == project_id),
            "artifacts": self._count(session, Artifact, Artifact.project_id == project_id),
            "media_assets": self._count(session, MediaAsset, MediaAsset.project_id == project_id),
            "jobs": self._count(session, Job, Job.project_id == project_id),
            "timeline_revisions": self._count(session, TimelineRevision, TimelineRevision.project_id == project_id),
            "content_revisions": self._count(session, ContentRevision, ContentRevision.project_id == project_id),
            "tts_generations": self._count(session, TTSGeneration, TTSGeneration.project_id == project_id),
            "projects": self._count(session, Project, Project.project_id == project_id),
        }

    def _count(self, session: Session, model: type, condition: Any) -> int:
        return int(session.query(model).filter(condition).count())

    def _delete_db_rows(self, session: Session, project_id: str) -> None:
        tracks = [row[0] for row in session.execute(select(SubtitleTrack.track_id).where(SubtitleTrack.project_id == project_id)).all()]
        if tracks:
            session.execute(delete(SubtitleTrackItem).where(SubtitleTrackItem.track_id.in_(tracks)))
        for model in (
            SubtitleTrack,
            SimpleWorkflowRun,
            Artifact,
            MediaAsset,
            Job,
            ContentRevision,
            TimelineRevision,
            TTSGeneration,
        ):
            session.execute(delete(model).where(model.project_id == project_id))
        session.execute(delete(Project).where(Project.project_id == project_id))

    def _read_ledger(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {}
        return json.loads(self.ledger_path.read_text(encoding="utf-8"))

    def _record_ledger(self, project_id: str, result: ProjectRetirementResult) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger = self._read_ledger()
        ledger[project_id] = {"retired_at": datetime.now(timezone.utc).isoformat(), "result": result.to_dict()}
        self.ledger_path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False), encoding="utf-8")
