from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from tests.test_cp10b_simple_workflow import configure_test_root


def _session():
    from app.db.session import SessionLocal, get_engine

    get_engine()
    return SessionLocal()


def _service(tmp_path: Path):
    from app.services.project_retirement import ProjectRetirementService

    projects_root = tmp_path / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    return ProjectRetirementService(root=tmp_path, projects_root=projects_root, ledger_path=tmp_path / "ledger.json")


def _seed_project(session, service, project_id: str = "fixture_project") -> Path:
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

    path = service.projects_root / project_id
    path.mkdir(parents=True)
    (path / "source.mp4").write_bytes(b"synthetic")
    session.add(Project(project_id=project_id, title="Fixture", is_test_fixture=True))
    session.add(TimelineRevision(project_id=project_id, revision_id="tl1", path=str(path / "timeline.json"), sha256="c" * 64))
    session.flush()
    session.add(Artifact(project_id=project_id, artifact_type="preview", path=str(path / "preview.mp4"), sha256="a" * 64))
    session.add(MediaAsset(project_id=project_id, source_sha256="b" * 64, path=str(path / "source.mp4"), duration_seconds="1"))
    session.add(Job(project_id=project_id, status="done", kind="fixture"))
    session.add(ContentRevision(project_id=project_id, revision_id="cr1", timeline_revision_id="tl1", provider_request_hash="h", path=str(path / "content.json"), sha256="d" * 64))
    session.add(TTSGeneration(project_id=project_id, segment_id="s1", generation_id=f"gen_{project_id}", provider="fake", model="fake", voice_id="v", request_hash="e" * 64, cache_status="miss", status="done", artifact_path=str(path / "tts.wav"), sha256="f" * 64, character_count=4))
    session.add(SimpleWorkflowRun(run_id=f"run_{project_id}", project_id=project_id, source_path=str(path / "source.mp4"), source_hash="b" * 64, source_metadata_json="{}", requested_settings_json="{}", current_phase="done", internal_state="complete", run_directory=str(path), output_path=str(path / "out.mp4"), output_hash="g" * 64, is_test_fixture=True))
    session.add(SubtitleTrack(track_id=f"track_{project_id}", project_id=project_id, run_id=f"run_{project_id}", track_type="translation", display_name="Translation", source_type="generated"))
    session.add(SubtitleTrackItem(item_id=f"item_{project_id}", track_id=f"track_{project_id}", cue_id="CUE_0001", text="hello"))
    session.commit()
    return path


def test_refuses_canonical_project_deletion(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    from app.db.session import init_db
    from app.services.project_retirement import ProjectRetirementError

    init_db()
    service = _service(tmp_path)
    with _session() as session, pytest.raises(ProjectRetirementError):
        service.retire_project(session, "vertical_slice_cp07", dry_run=True)


def test_refuses_unknown_project_id(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    from app.db.session import init_db
    from app.services.project_retirement import ProjectRetirementError

    init_db()
    service = _service(tmp_path)
    with _session() as session, pytest.raises(ProjectRetirementError):
        service.retire_project(session, "missing_project", dry_run=True)


def test_dry_run_does_not_mutate_db_or_filesystem(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    from app.db.session import init_db
    from app.domain.models import Project

    init_db()
    service = _service(tmp_path)
    with _session() as session:
        path = _seed_project(session, service)
        result = service.retire_project(session, "fixture_project", dry_run=True)
        assert result.status == "dry_run"
        assert path.exists()
        assert session.query(Project).filter(Project.project_id == "fixture_project").count() == 1


def test_filesystem_validation_failure_rolls_back_db(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    from app.db.session import init_db
    from app.domain.models import Project
    from app.services.project_retirement import ProjectRetirementError

    init_db()
    service = _service(tmp_path)
    with _session() as session:
        _seed_project(session, service)
        monkeypatch.setattr(service, "_validate_filesystem", lambda *args, **kwargs: (_ for _ in ()).throw(ProjectRetirementError("validation failed")))
        with pytest.raises(ProjectRetirementError):
            service.retire_project(session, "fixture_project", dry_run=False)
        session.rollback()
        assert session.query(Project).filter(Project.project_id == "fixture_project").count() == 1


def test_successful_synthetic_project_retirement_and_foreign_keys(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    from app.db.session import init_db
    from app.domain.models import Project

    init_db()
    service = _service(tmp_path)
    with _session() as session:
        path = _seed_project(session, service)
        result = service.retire_project(session, "fixture_project", dry_run=False)
        assert result.status == "retired"
        assert result.filesystem_removed is True
        assert not path.exists()
        assert session.query(Project).filter(Project.project_id == "fixture_project").count() == 0
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_repeated_retirement_call_is_idempotent(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    from app.db.session import init_db

    init_db()
    service = _service(tmp_path)
    with _session() as session:
        _seed_project(session, service)
        service.retire_project(session, "fixture_project", dry_run=False)
    with _session() as session:
        result = service.retire_project(session, "fixture_project", dry_run=False)
        assert result.status == "already_retired"


def test_filesystem_path_containment(monkeypatch, tmp_path):
    configure_test_root(monkeypatch, tmp_path)
    from app.db.session import init_db
    from app.services.project_retirement import ProjectRetirementError

    init_db()
    service = _service(tmp_path)
    with _session() as session, pytest.raises(ProjectRetirementError):
        service.retire_project(session, "../escape", dry_run=True)
