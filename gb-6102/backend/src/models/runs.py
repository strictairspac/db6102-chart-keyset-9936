from sqlalchemy import BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


# A receipt (回执) is one submitter's claim on a shared job result. The result
# itself is stored once on the job; a receipt tracks only its owner, its own
# lifecycle state, and whether the owner has withdrawn it.
runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
    Column("job_id", ForeignKey("jobs.id"), nullable=False, index=True),
    # Per-receipt lifecycle: queued (待领) / done (已就绪) / failed / cancelled.
    Column("status", String(16), nullable=False),
    Column("parameter", Integer, nullable=False),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    # Set when the owner withdraws this receipt. A withdrawal also moves the
    # receipt's own status to cancelled; the job lives on for other receipts.
    Column("cancelled_at", BigInteger, nullable=True),
)
