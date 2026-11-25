from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Index, Integer, String, Table
from .base import metadata


# One shared computation serves one or more receipts (runs rows) that submitted
# the same material with the same 每页条数. The result is stored here once.
jobs = Table(
    "jobs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False),
    Column("parameter", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    # Content-addressed coalescing key, set only while the job may still serve
    # a newcomer. Cleared atomically when the job reaches a final state so a
    # later submission starts a fresh computation instead of reading a stale
    # result. UNIQUE keeps concurrent identical submissions down to one job.
    Column("dedupe_key", String(64), unique=True, nullable=True),
    Column("attempts", Integer, nullable=False),
    # Fencing token of the single worker attempt allowed to publish a result.
    Column("lease_token", String(32), nullable=True),
    Column("lease_expires_at", BigInteger, nullable=True),
    Column("created_at", BigInteger, nullable=False),
    Column("started_at", BigInteger, nullable=True),
    Column("finished_at", BigInteger, nullable=True),
    Column("result", JSON(none_as_null=True), nullable=True),
    Column("failure", String(320), nullable=True),
    Column("digest", String(64), nullable=True),
)

# The worker scans for queued / expired-lease jobs in id order.
Index("jobs_dispatch", jobs.c.status, jobs.c.id)
