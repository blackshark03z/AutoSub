from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from app.db.session import init_db, session_scope
from app.domain.models import Project
from app.services.job_claims import claim_next_job, enqueue_job


def test_duplicate_enqueue_and_two_worker_claim_are_idempotent():
    project_id = f"proj_job_race_{uuid4().hex[:8]}"
    init_db()
    with session_scope() as session:
        session.add(Project(project_id=project_id, title=project_id))
    first = enqueue_job(project_id, "full_sample", "input-hash")
    second = enqueue_job(project_id, "full_sample", "input-hash")
    assert first["job_id"] == second["job_id"]
    assert (first["created"], second["created"]) == (True, False)

    barrier = Barrier(2)

    def worker(name):
        barrier.wait(timeout=2)
        return claim_next_job(name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = [pool.submit(worker, name) for name in ("worker-a", "worker-b")]
        results = [future.result(timeout=5) for future in claims]
    successful = [result for result in results if result is not None and result["job_id"] == first["job_id"]]
    assert len(successful) == 1
