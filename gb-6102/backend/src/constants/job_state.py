from enum import StrEnum


class JobState(StrEnum):
    """State of the shared background computation behind one or more receipts."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    # The last active receipt was withdrawn, so the computation is abandoned.
    # A worker that is still running it must discard any late result.
    CANCELLED = "cancelled"


# Receipt states deliberately reuse the same words as job states: a receipt is
# queued / done / failed / cancelled. done without having been collected is
# 已就绪; cancelled is 已撤销; queued is 待领.
ReceiptState = JobState
