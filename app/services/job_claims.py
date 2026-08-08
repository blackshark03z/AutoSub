from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app.core.canonical import canonical_hash
from app.db.session import session_scope
from app.domain.models import Job


def enqueue_job(project_id: str, kind: str, input_hash: str) -> dict:
    job_key = canonical_hash({"project_id": project_id, "kind": kind, "input_hash": input_hash})
    now = datetime.utcnow()
    try:
        with session_scope() as session:
            job = Job(
                project_id=project_id,
                kind=kind,
                status="queued",
                job_key=job_key,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.flush()
            job_id = job.id
        return {"job_id": job_id, "job_key": job_key, "status": "queued", "created": True}
    except IntegrityError:
        with session_scope() as session:
            job = session.query(Job).filter(Job.job_key == job_key).one()
            return {"job_id": job.id, "job_key": job.job_key, "status": job.status, "created": False}


def claim_next_job(owner_token: str, lease_seconds: float = 300.0) -> dict | None:
    with session_scope() as session:
        candidate = session.query(Job).filter(Job.status == "queued").order_by(Job.id.asc()).first()
        if candidate is None:
            return None
        now = datetime.utcnow()
        updated = (
            session.query(Job)
            .filter(Job.id == candidate.id, Job.status == "queued")
            .update(
                {
                    Job.status: "claimed",
                    Job.owner_token: owner_token,
                    Job.lease_expires_at: now + timedelta(seconds=lease_seconds),
                    Job.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            return None
        return {
            "job_id": candidate.id,
            "job_key": candidate.job_key,
            "project_id": candidate.project_id,
            "kind": candidate.kind,
            "status": "claimed",
            "owner_token": owner_token,
        }
