"""idempotent jobs and atomic claim metadata"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_job_claims"
down_revision: str | None = "0005_tts_single_flight"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("job_key", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("owner_token", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_unique_constraint("uq_jobs_job_key", ["job_key"])
        batch.create_index("ix_jobs_job_key", ["job_key"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_job_key")
        batch.drop_constraint("uq_jobs_job_key", type_="unique")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
        batch.drop_column("lease_expires_at")
        batch.drop_column("owner_token")
        batch.drop_column("job_key")
