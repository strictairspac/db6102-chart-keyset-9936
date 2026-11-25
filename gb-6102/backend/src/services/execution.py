import logging
import threading
from time import time
from uuid import uuid4

from sqlalchemy import func, select, update

from src.config.resources import engine
from src.constants.job_state import JobState, ReceiptState
from src.domain import scenario
from src.models.cases import cases
from src.models.jobs import jobs
from src.models.runs import runs
from src.services.audit import record
from src.utils.serial import content_digest

logger = logging.getLogger(__name__)

# A worker owns a job for this long at a time. While it runs, a heartbeat keeps
# extending the lease; when the worker dies the heartbeat stops and another
# worker reclaims the job within this window, so every still-valid receipt is
# eventually served. Computations are short, so a small window recovers fast.
LEASE_SECONDS = 10
HEARTBEAT_SECONDS = 3
MAX_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = 2


class JobFailure(Exception):
    def __init__(self, job_id, token, cause):
        super().__init__(str(cause))
        self.job_id = job_id
        self.token = token
        self.cause = cause


def _settle_exhausted(db, now):
    """Fail jobs that were interrupted too many times to ever finish."""
    doomed = db.execute(
        select(jobs.c.id).where(
            jobs.c.status == JobState.RUNNING,
            jobs.c.lease_expires_at.is_not(None),
            jobs.c.lease_expires_at < now,
            jobs.c.attempts >= MAX_ATTEMPTS,
        ).with_for_update(skip_locked=True)
    ).scalars().all()
    for job_id in doomed:
        db.execute(update(jobs).where(jobs.c.id == job_id).values(
            status=JobState.FAILED, failure="多次中断，未能完成本次计算",
            dedupe_key=None, lease_token=None, lease_expires_at=None, finished_at=now,
        ))
        db.execute(update(runs).where(
            runs.c.job_id == job_id, runs.c.status == ReceiptState.QUEUED,
        ).values(status=ReceiptState.FAILED))
        record(db, str(JobState.FAILED), "worker", "job", job_id)


def claim():
    """Lease the oldest outstanding job.

    Outstanding means queued, or running under an expired lease (the previous
    worker died or stalled). ``skip_locked`` lets several workers claim
    different jobs; the lease token fences whichever attempt may publish.
    """
    with engine.begin() as db:
        now = int(time())
        _settle_exhausted(db, now)
        job_id = db.execute(
            select(jobs.c.id).where(
                jobs.c.status.in_((JobState.QUEUED, JobState.RUNNING)),
                (jobs.c.lease_expires_at.is_(None)) | (jobs.c.lease_expires_at < now),
            ).order_by(jobs.c.id).limit(1).with_for_update(skip_locked=True)
        ).scalar()
        if job_id is None:
            return None
        token = uuid4().hex
        db.execute(update(jobs).where(jobs.c.id == job_id).values(
            status=JobState.RUNNING,
            lease_token=token,
            lease_expires_at=now + LEASE_SECONDS,
            started_at=func.coalesce(jobs.c.started_at, now),
            attempts=jobs.c.attempts + 1,
        ))
        details = db.execute(
            select(jobs.c.case_id, jobs.c.parameter, cases.c.payload)
            .join(cases, cases.c.id == jobs.c.case_id)
            .where(jobs.c.id == job_id)
        ).mappings().first()
        return {"job_id": job_id, "token": token, **details}


class Lease:
    """Keeps a claimed job's lease alive for the duration of a computation."""

    def __init__(self, job_id, token):
        self.job_id = job_id
        self.token = token
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join(timeout=HEARTBEAT_SECONDS + 1)

    def _beat(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                with engine.begin() as db:
                    # Extend only while this attempt still owns the job. Once
                    # cancellation or another worker changes the token, the
                    # heartbeat turns into a harmless no-op.
                    db.execute(update(jobs).where(
                        jobs.c.id == self.job_id,
                        jobs.c.status == JobState.RUNNING,
                        jobs.c.lease_token == self.token,
                    ).values(lease_expires_at=int(time()) + LEASE_SECONDS))
            except Exception:  # A missed beat is recoverable; retry on the next.
                logger.exception("Lease heartbeat failed for job %s", self.job_id)


def service_one():
    """Run one shared computation, if any is due. Returns whether work was done."""
    task = claim()
    if task is None:
        return False
    try:
        with Lease(task["job_id"], task["token"]):
            # Compute on a separate connection so a long run never holds the
            # job row lock; the heartbeat, not the transaction, guards the lease.
            with engine.connect() as db:
                with db.begin():
                    result = scenario.run(task["payload"], task["parameter"], db)
        output = {"status": JobState.DONE, "result": result, "digest": content_digest(result)}
    except Exception as exc:
        raise JobFailure(task["job_id"], task["token"], exc) from exc
    return publish(task["job_id"], task["token"], output)


def fail(job_failure):
    """Settle a failed attempt.

    Bad input fails the job for good. Transient trouble (database, worker
    crash) returns the job to the queue so a still-valid receipt is served
    after recovery, but repeated interruptions eventually fail it instead of
    spinning forever.
    """
    cause = job_failure.cause
    if isinstance(cause, (ValueError, TypeError, KeyError, OverflowError)):
        reason = str(cause) or "输入资料或批次参数不符合要求"
        publish(job_failure.job_id, job_failure.token, {"status": JobState.FAILED, "failure": reason[:320]})
    else:
        retry(job_failure.job_id, job_failure.token)


def retry(job_id, token):
    """Release a still-owned job back to the queue, or fail after too many tries."""
    with engine.begin() as db:
        now = int(time())
        row = db.execute(
            select(jobs.c.id, jobs.c.attempts).where(
                jobs.c.id == job_id,
                jobs.c.status == JobState.RUNNING,
                jobs.c.lease_token == token,
            ).with_for_update()
        ).mappings().first()
        if row is None:
            return False
        exhausted = row["attempts"] >= MAX_ATTEMPTS
        db.execute(update(jobs).where(jobs.c.id == job_id).values(
            **({"status": JobState.QUEUED} if not exhausted else {
                "status": JobState.FAILED, "failure": "多次中断，未能完成本次计算",
            }),
            lease_token=None,
            # A short backoff keeps a persistently failing job from spinning;
            # the job becomes claimable again once this moment passes.
            lease_expires_at=None if exhausted else now + RETRY_BACKOFF_SECONDS,
            dedupe_key=None if exhausted else jobs.c.dedupe_key,
            finished_at=now if exhausted else None,
        ))
        if exhausted:
            db.execute(update(runs).where(
                runs.c.job_id == job_id, runs.c.status == ReceiptState.QUEUED,
            ).values(status=ReceiptState.FAILED))
            record(db, str(JobState.FAILED), "worker", "job", job_id)
        return True


def publish(job_id, token, output):
    """Commit an outcome only if this attempt still owns the job.

    The fence is the lease token. If the last active receipt was withdrawn, the
    cancellation cleared the token and set the job to CANCELLED, so this update
    matches nothing and the late result is dropped. A worker that lost its lease
    to another attempt is fenced the same way.
    """
    with engine.begin() as db:
        now = int(time())
        change = db.execute(
            update(jobs).where(
                jobs.c.id == job_id,
                jobs.c.status == JobState.RUNNING,
                jobs.c.lease_token == token,
            ).values(
                status=output["status"],
                result=output.get("result"),
                digest=output.get("digest"),
                failure=output.get("failure"),
                finished_at=now,
                dedupe_key=None,
                lease_token=None,
                lease_expires_at=None,
            )
        )
        if change.rowcount == 0:
            logger.info("Discarded superseded result for job %s", job_id)
            return False
        # Every still-active receipt for the shared job becomes ready together.
        db.execute(
            update(runs).where(
                runs.c.job_id == job_id, runs.c.status == ReceiptState.QUEUED,
            ).values(status=ReceiptState(output["status"]))
        )
        record(db, str(output["status"]), "worker", "job", job_id)
        return True
