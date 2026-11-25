from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select, update

from src.constants.job_state import JobState, ReceiptState
from src.domain.scenario import CONFIG
from src.models.cases import cases
from src.models.jobs import jobs
from src.models.runs import runs
from src.utils.serial import content_digest
from . import audit, cases as case_service

# Only an in-flight job can absorb another submission; a finished or cancelled
# computation is never reused, so nobody can collect a stale late result.
LIVE_JOB_STATES = (JobState.QUEUED, JobState.RUNNING)


def coalesce_key(payload, parameter):
    """Content address of 资料 + 每页条数. Copies of one sample share a key."""
    return content_digest({"payload": payload, "parameter": parameter})


def query():
    return (
        select(
            runs,
            cases.c.title.label("case_title"),
            jobs.c.status.label("job_status"),
            jobs.c.result.label("job_result"),
            jobs.c.failure.label("job_failure"),
            jobs.c.digest.label("job_digest"),
            jobs.c.finished_at.label("job_finished_at"),
        )
        .join(cases, cases.c.id == runs.c.case_id)
        .join(jobs, jobs.c.id == runs.c.job_id)
    )


def entry(row):
    """A receipt owns no result of its own; it reads the shared job's result."""
    run = {
        "id": row["id"],
        "case_id": row["case_id"],
        "job_id": row["job_id"],
        "case_title": row["case_title"],
        "status": row["status"],
        "parameter": row["parameter"],
        "actor": row["actor"],
        "created_at": row["created_at"],
        "finished_at": None,
        "result": None,
        "failure": None,
        "digest": None,
    }
    if row["status"] == ReceiptState.DONE:
        run.update(result=row["job_result"], digest=row["job_digest"], finished_at=row["job_finished_at"])
    elif row["status"] == ReceiptState.FAILED:
        run.update(failure=row["job_failure"], finished_at=row["job_finished_at"])
    return run


def get(db, run_id, lock=False):
    statement = query().where(runs.c.id == run_id)
    if lock:
        # A locking read sees the latest committed rows. ``enqueue`` and
        # ``cancel`` need this: under MySQL's REPEATABLE READ a plain read
        # uses a snapshot from before the transaction's own writes and could
        # miss a job another submitter committed in between.
        statement = statement.with_for_update()
    run = db.execute(statement).mappings().first()
    if run is None:
        raise HTTPException(404, "执行回执不存在")
    return entry(run)


def list_entries(db):
    return [entry(row) for row in db.execute(query().order_by(runs.c.id.desc()).limit(100)).mappings()]


def _live_job(db, key):
    """Fetch (locking) the in-flight equivalent job id, if any.

    A job's ``dedupe_key`` is cleared in the same statement that gives it a
    final state, so a row still carrying the key is always in flight.
    """
    return db.execute(
        select(jobs.c.id).where(jobs.c.dedupe_key == key).with_for_update()
    ).scalar()


def _upsert_job(db, values):
    """Atomically return the equivalent in-flight job, creating one if absent.

    A plain check-then-insert races under concurrent submissions and would also
    read a stale snapshot under MySQL's REPEATABLE READ. Insert-on-conflict lets
    the database resolve the unique ``dedupe_key``: the first submission creates
    the job, every simultaneous one joins it, and each still gets its own
    receipt.

    The lookup after the upsert is a locking read, so it sees any concurrently
    committed row. If the matched job finished in between (its key was cleared),
    retry: that submission now starts a fresh computation rather than reusing a
    settled one.
    """
    key = values["dedupe_key"]
    dialect = db.dialect.name
    if dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        statement = mysql_insert(jobs).values(**values).on_duplicate_key_update(id=jobs.c.id)
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = pg_insert(jobs).values(**values).on_conflict_do_nothing(index_elements=["dedupe_key"])
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        statement = sqlite_insert(jobs).values(**values).on_conflict_do_nothing(index_elements=["dedupe_key"])
    for _ in range(5):
        db.execute(statement)
        job_id = _live_job(db, key)
        if job_id is not None:
            return job_id
    raise HTTPException(409, "同一资料的复演正在收尾，请重新提交")


def _join_or_start(db, case_id, parameter, key, now):
    """Attach a new receipt to the in-flight equivalent job, else start one."""
    return _upsert_job(db, {
        "case_id": case_id, "parameter": parameter, "status": JobState.QUEUED,
        "dedupe_key": key, "attempts": 0, "created_at": now,
    })


def enqueue(db, case_id, parameter, actor):
    if parameter > CONFIG["max_parameter"]:
        raise HTTPException(422, f"参数不能超过 {CONFIG['max_parameter']}")
    material = case_service.get(db, case_id)
    now = int(time())
    job_id = _join_or_start(db, case_id, parameter, coalesce_key(material["payload"], parameter), now)
    change = db.execute(insert(runs).values(
        case_id=case_id, job_id=job_id, status=ReceiptState.QUEUED,
        parameter=parameter, actor=actor, created_at=now,
    ))
    run_id = change.inserted_primary_key[0]
    audit.record(db, "enqueue", actor, "run", run_id)
    return get(db, run_id, lock=True)


def cancel(db, run_id, actor):
    """Withdraw one receipt; never another person's.

    When the last valid receipt for a job is withdrawn the shared job is
    cancelled and its coalescing key is released. A new submission then starts
    a fresh computation, and the abandoned worker's late result is fenced off
    because it may only publish into a job that is still RUNNING.
    """
    receipt = db.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
    if receipt is None:
        raise HTTPException(404, "执行回执不存在")
    if receipt["actor"] != actor:
        raise HTTPException(403, "只能撤销自己的执行回执")
    if receipt["status"] == ReceiptState.CANCELLED:
        return get(db, run_id, lock=True)
    # Lock the shared job before the receipt: the worker publishes job-first,
    # so this ordering cannot deadlock with it.
    job = db.execute(
        select(jobs.c.id, jobs.c.status).where(jobs.c.id == receipt["job_id"]).with_for_update()
    ).mappings().first()
    receipt = db.execute(select(runs).where(runs.c.id == run_id).with_for_update()).mappings().first()
    if receipt["status"] == ReceiptState.CANCELLED:
        return get(db, run_id, lock=True)
    now = int(time())
    db.execute(update(runs).where(runs.c.id == run_id).values(
        status=ReceiptState.CANCELLED, cancelled_at=now,
    ))
    audit.record(db, "cancel_run", actor, "run", run_id)
    # Count with a locking read: under MySQL's REPEATABLE READ a plain SELECT
    # would use the snapshot taken before the job lock and could miss a receipt
    # that another submitter just added. Every enqueue holds the job row lock
    # until its receipt is inserted, so holding that lock here serializes them.
    active = len(db.execute(
        select(runs.c.id).where(
            runs.c.job_id == receipt["job_id"], runs.c.status != ReceiptState.CANCELLED,
        ).with_for_update()
    ).scalars().all())
    if active == 0 and job is not None and job["status"] in LIVE_JOB_STATES:
        db.execute(update(jobs).where(jobs.c.id == job["id"]).values(
            status=JobState.CANCELLED, dedupe_key=None, lease_token=None, lease_expires_at=None,
            result=None, digest=None, failure=None, finished_at=now,
        ))
        audit.record(db, "cancel_job", actor, "job", job["id"])
    return get(db, run_id, lock=True)
