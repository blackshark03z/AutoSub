import json
import os
import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.hashing import sha256_file
from app.core.media import media_summary
from app.core.paths import ensure_dir, ensure_within_root
from app.db.session import session_scope
from app.domain.models import MediaAsset
from app.services.artifacts import register_artifact


def import_local_source(project_id: str, source_path: Path) -> dict:
    settings = get_settings()
    source = ensure_within_root(settings.root, source_path)
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")

    project_source_dir = ensure_dir(settings.data_dir / "projects" / project_id / "source")
    destination = project_source_dir / "input.mp4"
    temp_destination = project_source_dir / "input.mp4.tmp"
    try:
        summary = media_summary(source)
        shutil.copy2(source, temp_destination)
        os.replace(temp_destination, destination)
        digest = sha256_file(destination)
        provenance = json.loads(settings.provenance_path.read_text(encoding="utf-8"))
        provenance["source_sha256"] = digest
        provenance["imported_path"] = str(destination)
        provenance_path = project_source_dir / "source_provenance.json"
        provenance_path.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    except Exception:
        if temp_destination.exists():
            temp_destination.unlink()
        if destination.exists():
            destination.unlink()
        raise

    with session_scope() as session:
        session.add(
            MediaAsset(
                project_id=project_id,
                source_sha256=digest,
                path=str(destination),
                duration_seconds=str(summary["duration_seconds"]),
                width=summary["video"].get("width"),
                height=summary["video"].get("height"),
            )
        )

    source_artifact = register_artifact(project_id, "source_video", destination)
    provenance_artifact = register_artifact(project_id, "source_provenance", provenance_path)
    return {
        "project_id": project_id,
        "source": source_artifact,
        "provenance": provenance_artifact,
        "media": summary,
    }
