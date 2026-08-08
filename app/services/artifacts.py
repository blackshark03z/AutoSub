from pathlib import Path

from app.core.hashing import sha256_file
from app.db.session import session_scope
from app.domain.models import Artifact


def register_artifact(project_id: str, artifact_type: str, path: Path) -> dict:
    digest = sha256_file(path)
    with session_scope() as session:
        session.add(Artifact(project_id=project_id, artifact_type=artifact_type, path=str(path), sha256=digest))
    return {"path": str(path), "sha256": digest}
